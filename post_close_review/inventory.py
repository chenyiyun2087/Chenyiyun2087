from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class InventoryRecord:
    symbol: str
    in_date: pd.Timestamp
    in_price: float
    pivot_price: float
    status: str = "in"


class InventoryStateMachine:
    def __init__(self) -> None:
        self.state = pd.DataFrame(
            columns=[
                "symbol",
                "in_date",
                "in_price",
                "pivot_price",
                "last_date",
                "last_close",
                "ret_since_in",
                "max_ret_since_in",
                "max_dd_since_in",
                "status",
                "out_date",
                "out_reason",
            ]
        )

    def update(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        daily_df = daily_df.copy()
        date = daily_df["trade_date"].iloc[0]

        # in-place updates for existing positions
        if not self.state.empty:
            active = self.state[self.state["status"] == "in"].set_index("symbol")
            now = daily_df.set_index("symbol")
            overlap = active.index.intersection(now.index)
            for sym in overlap:
                close = float(now.at[sym, "close"])
                in_price = float(active.at[sym, "in_price"])
                ret = close / in_price - 1.0
                prev_max = float(active.at[sym, "max_ret_since_in"])
                max_ret = max(prev_max, ret)
                dd = ret - max_ret
                idx = self.state.index[self.state["symbol"] == sym][0]
                self.state.loc[idx, ["last_date", "last_close", "ret_since_in", "max_ret_since_in", "max_dd_since_in"]] = [
                    date,
                    close,
                    ret,
                    max_ret,
                    dd,
                ]

        # sell
        for _, row in daily_df[daily_df["sell_signal"]].iterrows():
            mask = (self.state["symbol"] == row["symbol"]) & (self.state["status"] == "in")
            if mask.any():
                self.state.loc[mask, ["status", "out_date", "out_reason"]] = ["out", date, "sell_signal"]

        # buy (only if not in inventory)
        active_symbols = set(self.state.loc[self.state["status"] == "in", "symbol"].tolist())
        buys = daily_df[daily_df["buy_signal"] & ~daily_df["symbol"].isin(active_symbols)]
        if not buys.empty:
            additions = pd.DataFrame(
                {
                    "symbol": buys["symbol"].values,
                    "in_date": date,
                    "in_price": buys["close"].values,
                    "pivot_price": buys["pivot_price"].values,
                    "last_date": date,
                    "last_close": buys["close"].values,
                    "ret_since_in": 0.0,
                    "max_ret_since_in": 0.0,
                    "max_dd_since_in": 0.0,
                    "status": "in",
                    "out_date": pd.NaT,
                    "out_reason": "",
                }
            )
            self.state = pd.concat([self.state, additions], ignore_index=True)

        return self.state.copy()

    def active_symbols(self) -> list[str]:
        if self.state.empty:
            return []
        return self.state.loc[self.state["status"] == "in", "symbol"].tolist()
