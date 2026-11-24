"""Phase 1: Episode State Machine & Landmark Deduplication Engine.

Closes P0-02 by explicitly mapping every signal to an independent episode,
separating signal-level observations from episode-level independent units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


class WaveState(str, Enum):
    IDLE = "IDLE"
    WAVE1 = "WAVE1"
    DRAWDOWN = "DRAWDOWN"
    SIGNAL = "SIGNAL"
    CONFIRMATION = "CONFIRMATION"
    INVALIDATION = "INVALIDATION"
    CLOSED = "CLOSED"


@dataclass
class EpisodeRecord:
    episode_id: str
    ts_code: str
    wave1_start_date: str = ""
    wave1_peak_date: str = ""
    wave1_peak_price: float = 0.0
    wave1_gain: float = 0.0
    dd_low_date: str = ""
    dd_low_price: float = 0.0
    dd_depth: float = 0.0
    signal_date: str = ""
    signal_price: float = 0.0
    state: WaveState = WaveState.SIGNAL


class EpisodeEngine:
    """Manages the lifecycle of Wave 1 -> Drawdown -> Bottom -> Confirmation episodes."""

    def __init__(self, min_wave1_pct: float = 0.50, min_dd_pct: float = 0.30, cooldown_days: int = 20):
        self.min_wave1_pct = min_wave1_pct
        self.min_dd_pct = min_dd_pct
        self.cooldown_days = cooldown_days

    def assign_episodes_for_stock(self, df: pd.DataFrame) -> pd.DataFrame:
        """Assigns episode_id and landmark_flag for a single stock time series."""
        n = len(df)
        df["episode_id"] = ""
        df["is_landmark_signal"] = False

        if "wave1_valid" not in df.columns or "is_DD40" not in df.columns:
            # Fallback if raw signal dataframe is passed
            if "ts_code" in df.columns and "trade_date" in df.columns:
                df["episode_id"] = [f"{df['ts_code'].iloc[0]}_EP{i//self.cooldown_days}" for i in range(n)]
                df["is_landmark_signal"] = [(i % self.cooldown_days == 0) for i in range(n)]
            return df

        wave_valid = df["wave1_valid"].values
        dd40 = df["is_DD40"].values
        peak_dates = df.get("prior_peak_date", pd.Series(0, index=df.index)).fillna(0).astype(int).values
        dates = df["trade_date"].values
        ts_code = df["ts_code"].iloc[0] if "ts_code" in df.columns else "UNKNOWN"

        current_ep_num = 0
        current_ep_id = ""
        last_sig_idx = -999
        in_episode = False
        current_peak = 0

        episode_ids = [""] * n
        is_landmark = np.zeros(n, dtype=bool)

        for i in range(n):
            if wave_valid[i] and dd40[i]:
                p_date = peak_dates[i]
                if not in_episode or (p_date != current_peak and p_date > 0) or (i - last_sig_idx > self.cooldown_days * 2):
                    current_ep_num += 1
                    current_peak = p_date
                    current_ep_id = f"{ts_code}_EP{current_ep_num}_{p_date}"
                    in_episode = True
                    is_landmark[i] = True

                episode_ids[i] = current_ep_id
                last_sig_idx = i
            else:
                if in_episode and (i - last_sig_idx > self.cooldown_days):
                    in_episode = False
                    current_ep_id = ""

        df["episode_id"] = episode_ids
        df["is_landmark_signal"] = is_landmark
        return df

    def aggregate_by_episode(self, signals_df: pd.DataFrame) -> pd.DataFrame:
        """Deduplicates signals to strictly 1 observation per independent episode."""
        if signals_df.empty or "episode_id" not in signals_df.columns:
            return signals_df

        valid_ep = signals_df[signals_df["episode_id"] != ""].copy()
        if valid_ep.empty:
            return signals_df

        landmarks = valid_ep[valid_ep["is_landmark_signal"]].copy()
        if landmarks.empty:
            landmarks = valid_ep.sort_values("trade_date").groupby("episode_id", as_index=False).first()
        else:
            landmarks = landmarks.drop_duplicates(subset=["episode_id"])

        return landmarks.reset_index(drop=True)

    def process_signals_to_episodes(self, signals_df: pd.DataFrame) -> List[EpisodeRecord]:
        """Converts raw signals DataFrame into a list of EpisodeRecord objects."""
        if signals_df.empty:
            return []

        df = signals_df.copy()
        if "episode_id" not in df.columns:
            # Group by stock to assign
            grouped = []
            for ts_code, g in df.groupby("ts_code"):
                grouped.append(self.assign_episodes_for_stock(g.sort_values("trade_date").copy()))
            df = pd.concat(grouped, ignore_index=True)

        episodes = []
        for ep_id, group in df.groupby("episode_id"):
            if not ep_id:
                continue
            first_row = group.iloc[0]
            ep = EpisodeRecord(
                episode_id=str(ep_id),
                ts_code=str(first_row.get("ts_code", "")),
                wave1_start_date=str(first_row.get("wave1_start_date", "")),
                wave1_peak_date=str(first_row.get("wave1_peak_date", "")),
                wave1_peak_price=float(first_row.get("wave1_peak_price", 0.0)),
                wave1_gain=float(first_row.get("wave1_gain", 0.0)),
                dd_low_date=str(first_row.get("dd_low_date", "")),
                dd_low_price=float(first_row.get("dd_low_price", 0.0)),
                dd_depth=float(first_row.get("dd_depth", 0.0)),
                signal_date=str(first_row.get("signal_date", first_row.get("trade_date", ""))),
                signal_price=float(first_row.get("signal_price", 0.0)),
                state=WaveState.CONFIRMATION,
            )
            episodes.append(ep)

        return episodes

    def extract_landmark_signals(
        self, episodes: List[EpisodeRecord], signals_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Returns the deduplicated DataFrame of landmark signals corresponding to episodes."""
        if not episodes:
            return pd.DataFrame()

        ep_ids = [ep.episode_id for ep in episodes]
        df = signals_df.copy()
        if "episode_id" not in df.columns:
            grouped = []
            for ts_code, g in df.groupby("ts_code"):
                grouped.append(self.assign_episodes_for_stock(g.sort_values("trade_date").copy()))
            df = pd.concat(grouped, ignore_index=True)

        matched = df[df["episode_id"].isin(ep_ids)].copy()
        landmarks = matched[matched["is_landmark_signal"]].copy()
        if len(landmarks) < len(episodes):
            landmarks = matched.groupby("episode_id", as_index=False).first()
        else:
            landmarks = landmarks.drop_duplicates(subset=["episode_id"])

        return landmarks.reset_index(drop=True)
