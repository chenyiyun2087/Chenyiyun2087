from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from scoreRank.core.db_config import symbols_to_ts_codes


ADC_SCHEMA = "tushare_stock"


@dataclass(frozen=True)
class AShareFeatureTable:
    table: str
    columns: dict[str, str]
    date_column: str = "trade_date"
    ts_code_column: str = "ts_code"


ADC_FEATURE_TABLES = [
    AShareFeatureTable(
        "ads_features_stock_daily",
        {
            "ret_5": "adc_ret_5",
            "ret_20": "adc_ret_20",
            "ret_60": "adc_ret_60",
            "vol_20": "adc_vol_20",
            "vol_60": "adc_vol_60",
            "amt_ma20": "adc_amt_ma20",
            "turnover_rate": "adc_turnover_rate",
            "pe_ttm": "adc_pe_ttm",
            "pb": "adc_pb",
            "total_mv": "adc_total_mv",
            "circ_mv": "adc_circ_mv",
            "roe": "adc_roe",
            "grossprofit_margin": "adc_grossprofit_margin",
            "debt_to_assets": "adc_debt_to_assets",
            "industry_code": "adc_industry_code",
        },
    ),
    AShareFeatureTable(
        "dws_tech_pattern",
        {
            "hma_slope": "adc_hma_slope",
            "rsi_14": "adc_rsi_14",
            "boll_width": "adc_boll_width",
            "boll_upper": "adc_boll_upper",
            "boll_mid": "adc_boll_mid",
            "boll_lower": "adc_boll_lower",
        },
    ),
    AShareFeatureTable(
        "dws_capital_flow",
        {
            "main_net_ratio": "adc_main_net_ratio",
            "main_net_ma5": "adc_main_net_ma5",
            "vol_price_corr": "adc_vol_price_corr",
        },
    ),
    AShareFeatureTable(
        "dws_leverage_sentiment",
        {
            "rz_buy_intensity": "adc_rz_buy_intensity",
            "rq_pressure_factor": "adc_rq_pressure_factor",
            "turnover_spike": "adc_turnover_spike",
        },
    ),
    AShareFeatureTable(
        "dws_chip_dynamics",
        {
            "profit_ratio": "adc_profit_ratio",
            "profit_pressure": "adc_profit_pressure",
            "support_strength": "adc_support_strength",
            "chip_peak_cross": "adc_chip_peak_cross",
        },
    ),
    AShareFeatureTable(
        "dws_liquidity_factor",
        {
            "amihud_20": "adc_amihud_20",
            "turnover_vol_20": "adc_turnover_vol_20",
            "vol_concentration": "adc_vol_concentration",
            "bid_ask_spread": "adc_bid_ask_spread",
        },
    ),
    AShareFeatureTable(
        "dws_risk_factor",
        {
            "downside_vol_60": "adc_downside_vol_60",
            "max_drawdown_60": "adc_max_drawdown_60",
        },
    ),
    AShareFeatureTable(
        "ads_stock_score_daily",
        {
            "tech_score": "adc_tech_score",
            "capital_score": "adc_capital_score",
            "sentiment_score": "adc_sentiment_score",
            "chip_score": "adc_chip_score",
            "total_score": "adc_total_score",
            "score_rank": "adc_score_rank",
        },
    ),
    AShareFeatureTable(
        "ods_stk_factor",
        {
            "macd_qfq": "adc_macd_qfq",
            "macd_dif_qfq": "adc_macd_dif_qfq",
            "macd_dea_qfq": "adc_macd_dea_qfq",
            "kdj_qfq": "adc_kdj_qfq",
            "kdj_k_qfq": "adc_kdj_k_qfq",
            "kdj_d_qfq": "adc_kdj_d_qfq",
            "rsi_qfq_6": "adc_rsi_qfq_6",
            "rsi_qfq_12": "adc_rsi_qfq_12",
            "rsi_qfq_24": "adc_rsi_qfq_24",
            "boll_upper_qfq": "adc_boll_upper_qfq",
            "boll_mid_qfq": "adc_boll_mid_qfq",
            "boll_lower_qfq": "adc_boll_lower_qfq",
            "cci_qfq": "adc_cci_qfq",
            "atr_qfq": "adc_atr_qfq",
            "mfi_qfq": "adc_mfi_qfq",
            "mtm_qfq": "adc_mtm_qfq",
            "obv_qfq": "adc_obv_qfq",
            "roc_qfq": "adc_roc_qfq",
            "wr_qfq": "adc_wr_qfq",
            "dmi_adx_qfq": "adc_dmi_adx_qfq",
            "dmi_pdi_qfq": "adc_dmi_pdi_qfq",
            "dmi_mdi_qfq": "adc_dmi_mdi_qfq",
            "ma_qfq_5": "adc_ma_qfq_5",
            "ma_qfq_10": "adc_ma_qfq_10",
            "ma_qfq_20": "adc_ma_qfq_20",
            "ma_qfq_60": "adc_ma_qfq_60",
            "ma_qfq_250": "adc_ma_qfq_250",
            "updays": "adc_updays",
            "downdays": "adc_downdays",
            "lowdays": "adc_lowdays",
            "topdays": "adc_topdays",
            "volume_ratio": "adc_volume_ratio",
            "turnover_rate_f": "adc_turnover_rate_f",
            "amount": "adc_amount",
            "pct_chg": "adc_pct_chg",
        },
    ),
    AShareFeatureTable(
        "ads_stock_bs_signal",
        {
            "signal_strength": "adc_signal_strength",
            "signal_quality_score": "adc_signal_quality_score",
            "confirmation_required_flag": "adc_confirmation_required_flag",
            "execution_ready_flag": "adc_execution_ready_flag",
            "trend_context_score": "adc_trend_context_score",
            "volume_confirm_score": "adc_volume_confirm_score",
            "signal_price_confirm_flag": "adc_signal_price_confirm_flag",
            "signal_volume_confirm_flag": "adc_signal_volume_confirm_flag",
            "signal_kdj_confirm_flag": "adc_signal_kdj_confirm_flag",
            "secondary_indicator_score": "adc_secondary_indicator_score",
            "signal_geometry_score": "adc_signal_geometry_score",
            "trend_alignment_score": "adc_trend_alignment_score",
            "price_confirmation_score": "adc_price_confirmation_score",
            "volume_confirmation_detail_score": "adc_volume_confirmation_detail_score",
            "volatility_penalty_score": "adc_volatility_penalty_score",
            "plate_confirm_flag": "adc_plate_confirm_flag",
            "plate_mainline_score": "adc_plate_mainline_score",
            "plate_sentiment_score": "adc_plate_sentiment_score",
            "entry_signal_confidence": "adc_entry_signal_confidence",
            "exit_signal_confidence": "adc_exit_signal_confidence",
        },
    ),
]

ADC_FEATURE_COLUMNS = [
    target
    for table in ADC_FEATURE_TABLES
    for target in table.columns.values()
]

ADC_DERIVED_FEATURE_COLUMNS = [
    "adc_close_vs_ma20",
    "adc_close_vs_ma60",
    "adc_ma20_vs_ma60",
    "adc_boll_position",
    "adc_main_net_accel",
    "adc_leverage_balance",
    "adc_capital_leverage_resonance",
    "adc_capital_short_pressure",
    "adc_financing_turnover_resonance",
    "adc_score_momentum_gap",
]

ALL_ADC_FEATURE_COLUMNS = [*ADC_FEATURE_COLUMNS, *ADC_DERIVED_FEATURE_COLUMNS]


def _empty_features(symbols: list[str], date_keys: list[int]) -> pd.DataFrame:
    keys = pd.MultiIndex.from_product([sorted(set(symbols)), sorted(set(date_keys))], names=["symbol", "event_date_key"])
    frame = keys.to_frame(index=False)
    for col in ALL_ADC_FEATURE_COLUMNS:
        frame[col] = None
    return frame


def _safe_read(query_fn: Callable[[str, object | None], pd.DataFrame], sql: str, params=None) -> pd.DataFrame:
    try:
        return query_fn(sql, params)
    except Exception:
        return pd.DataFrame()


def _existing_columns(
    query_fn: Callable[[str, object | None], pd.DataFrame],
    schema: str,
    table: str,
) -> set[str]:
    sql = """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema = %s
      AND table_name = %s
    """
    rows = _safe_read(query_fn, sql, (schema, table))
    if rows.empty:
        return set()
    rows = rows.rename(columns={c: str(c).lower() for c in rows.columns})
    if "column_name" not in rows.columns:
        return set()
    return {str(v) for v in rows["column_name"].dropna().tolist()}


def _normalize_event_date_key(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.replace("-", "", regex=False).str.extract(r"(\d{8})", expand=False)
    return pd.to_numeric(raw, errors="coerce").astype("Int64")


def _normalize_symbol(series: pd.Series) -> pd.Series:
    raw = series.astype(str).str.extract(r"(\d+)", expand=False).fillna("")
    return raw.str[-6:].str.zfill(6)


def _numeric_columns(frame: pd.DataFrame, skip: set[str]) -> list[str]:
    return [
        col
        for col in frame.columns
        if col not in skip and not col.endswith("_code") and frame[col].dtype != object
    ]


def _read_table_features(
    spec: AShareFeatureTable,
    ts_codes: list[str],
    date_keys: list[int],
    query_fn: Callable[[str, object | None], pd.DataFrame],
    schema: str = ADC_SCHEMA,
) -> pd.DataFrame:
    if not ts_codes or not date_keys:
        return pd.DataFrame()
    existing = _existing_columns(query_fn, schema, spec.table)
    required_keys = {spec.date_column, spec.ts_code_column}
    if not required_keys.issubset(existing):
        return pd.DataFrame()
    selected = [(source, target) for source, target in spec.columns.items() if source in existing]
    if not selected:
        return pd.DataFrame()

    ts_placeholders = ",".join(["%s"] * len(ts_codes))
    select_cols = [
        f"{spec.ts_code_column} AS adc_ts_code",
        f"REPLACE(CAST({spec.date_column} AS CHAR), '-', '') AS adc_event_date_key",
        *[f"{source} AS {target}" for source, target in selected],
    ]
    sql = f"""
    SELECT {", ".join(select_cols)}
    FROM {schema}.{spec.table}
    WHERE {spec.date_column} >= %s
      AND {spec.date_column} <= %s
      AND {spec.ts_code_column} IN ({ts_placeholders})
    """
    params = [min(date_keys), max(date_keys), *ts_codes]
    frame = _safe_read(query_fn, sql, tuple(params))
    if frame.empty:
        return frame
    frame["symbol"] = _normalize_symbol(frame["adc_ts_code"])
    frame["event_date_key"] = _normalize_event_date_key(frame["adc_event_date_key"])
    frame = frame[frame["event_date_key"].notna()].copy()
    frame["event_date_key"] = frame["event_date_key"].astype(int)
    frame = frame.drop(columns=["adc_ts_code", "adc_event_date_key"], errors="ignore")
    frame = frame.drop_duplicates(["symbol", "event_date_key"], keep="last")
    return frame


def load_adc_features(
    symbols: list[str],
    date_keys: list[int],
    query_fn: Callable[[str, object | None], pd.DataFrame],
) -> pd.DataFrame:
    symbols = sorted({str(s).zfill(6) for s in symbols if str(s or "").strip()})
    date_keys = sorted({int(d) for d in date_keys if pd.notna(d)})
    if not symbols or not date_keys:
        return _empty_features(symbols, date_keys)

    ts_codes = symbols_to_ts_codes(symbols)
    merged = pd.MultiIndex.from_product([symbols, date_keys], names=["symbol", "event_date_key"]).to_frame(index=False)
    for spec in ADC_FEATURE_TABLES:
        table_features = _read_table_features(spec, ts_codes, date_keys, query_fn)
        if table_features.empty:
            continue
        merged = merged.merge(table_features, on=["symbol", "event_date_key"], how="left")

    for col in ADC_FEATURE_COLUMNS:
        if col not in merged.columns:
            merged[col] = None
    merged = _add_derived_features(merged)
    for col in ALL_ADC_FEATURE_COLUMNS:
        if col not in merged.columns:
            merged[col] = None
    return merged[["symbol", "event_date_key", *ALL_ADC_FEATURE_COLUMNS]]


def _add_derived_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    skip = {"symbol", "event_date_key", "adc_industry_code"}
    for col in _numeric_columns(out, skip):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    close_proxy = out.get("adc_ma_qfq_5")
    ma20 = out.get("adc_ma_qfq_20")
    ma60 = out.get("adc_ma_qfq_60")
    boll_upper = out.get("adc_boll_upper")
    boll_mid = out.get("adc_boll_mid")
    boll_lower = out.get("adc_boll_lower")
    if boll_upper is None:
        boll_upper = out.get("adc_boll_upper_qfq")
    if boll_mid is None:
        boll_mid = out.get("adc_boll_mid_qfq")
    if boll_lower is None:
        boll_lower = out.get("adc_boll_lower_qfq")

    if close_proxy is not None and ma20 is not None:
        out["adc_close_vs_ma20"] = (close_proxy - ma20) / ma20.replace(0, pd.NA)
    if close_proxy is not None and ma60 is not None:
        out["adc_close_vs_ma60"] = (close_proxy - ma60) / ma60.replace(0, pd.NA)
    if ma20 is not None and ma60 is not None:
        out["adc_ma20_vs_ma60"] = (ma20 - ma60) / ma60.replace(0, pd.NA)
    if close_proxy is not None and boll_upper is not None and boll_lower is not None:
        out["adc_boll_position"] = (close_proxy - boll_lower) / (boll_upper - boll_lower).replace(0, pd.NA)

    if {"adc_main_net_ratio", "adc_main_net_ma5"}.issubset(out.columns):
        out["adc_main_net_accel"] = out["adc_main_net_ratio"] - out["adc_main_net_ma5"]
    if {"adc_rz_buy_intensity", "adc_rq_pressure_factor"}.issubset(out.columns):
        out["adc_leverage_balance"] = out["adc_rz_buy_intensity"] - out["adc_rq_pressure_factor"]
    if {"adc_main_net_ratio", "adc_rz_buy_intensity"}.issubset(out.columns):
        out["adc_capital_leverage_resonance"] = out["adc_main_net_ratio"] * out["adc_rz_buy_intensity"]
    if {"adc_main_net_ratio", "adc_rq_pressure_factor"}.issubset(out.columns):
        out["adc_capital_short_pressure"] = out["adc_main_net_ratio"] - out["adc_rq_pressure_factor"]
    if {"adc_rz_buy_intensity", "adc_turnover_spike"}.issubset(out.columns):
        out["adc_financing_turnover_resonance"] = out["adc_rz_buy_intensity"] * out["adc_turnover_spike"]
    if {"adc_tech_score", "adc_chip_score"}.issubset(out.columns):
        out["adc_score_momentum_gap"] = out["adc_tech_score"] - out["adc_chip_score"]
    return out


def attach_adc_features(
    df: pd.DataFrame,
    date_key_col: str,
    query_fn: Callable[[str, object | None], pd.DataFrame],
) -> pd.DataFrame:
    out = df.copy()
    if out.empty or "symbol" not in out.columns or date_key_col not in out.columns:
        for col in ALL_ADC_FEATURE_COLUMNS:
            if col not in out.columns:
                out[col] = None
        return out

    out["symbol"] = _normalize_symbol(out["symbol"])
    out[date_key_col] = pd.to_numeric(out[date_key_col], errors="coerce").astype("Int64")
    features = load_adc_features(
        out["symbol"].dropna().astype(str).tolist(),
        out[date_key_col].dropna().astype(int).tolist(),
        query_fn,
    )
    features = features.rename(columns={"event_date_key": date_key_col})
    return out.drop(columns=[c for c in ALL_ADC_FEATURE_COLUMNS if c in out.columns], errors="ignore").merge(
        features,
        on=["symbol", date_key_col],
        how="left",
    )
