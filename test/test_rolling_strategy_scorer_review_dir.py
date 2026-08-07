"""Rolling strategy scorer review-dir discovery (hermetic, no DB).

Regression for the 2026-08-07 staleness fix: run_daily_strategy_backtest
writes <ts>_<us>_trusted_account_backtest directories (and
trusted_account_backtest_nav.csv / _summary.csv files), while the scorer
used to match only old-style production_all_strategy_review directories
and nav.csv / strategy_summary.csv names — leaving the score frozen at the
last old-style dir (2026-06-28).

Required cases:
  - new-style dir is selected when it is the latest
  - old-style dir still selected when no new-style exists
  - prefixed CSV names are accepted alongside plain names
  - summary columns missing (sharpe/calmar/completed_round_trips) are
    derived from buy/sell counts and annualized_return/max_drawdown
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_spec = importlib.util.spec_from_file_location(
    "run_rolling_strategy_scorer",
    PROJECT_ROOT / "scripts/ops/run_rolling_strategy_scorer.py",
)
_scorer = importlib.util.module_from_spec(_spec)
sys.modules["run_rolling_strategy_scorer"] = _scorer  # dataclass 导入期检查需要
_spec.loader.exec_module(_scorer)


def _make_dir(tmp: Path, name: str, with_nav: bool = True) -> Path:
    d = tmp / name
    d.mkdir()
    if with_nav:
        with open(d / "trusted_account_backtest_nav.csv", "w") as f:
            w = csv.writer(f)
            w.writerow(["strategy", "trade_date", "nav"])
            w.writerow(["s1", "2026-07-01", "1.0"])
            w.writerow(["s1", "2026-07-02", "1.01"])
            w.writerow(["s1", "2026-07-03", "1.02"])
            w.writerow(["s1", "2026-07-06", "1.03"])
            w.writerow(["s1", "2026-07-07", "1.04"])
            w.writerow(["s1", "2026-07-08", "1.05"])
            w.writerow(["s1", "2026-07-09", "1.06"])
            w.writerow(["s1", "2026-07-10", "1.07"])
            w.writerow(["s1", "2026-07-13", "1.08"])
            w.writerow(["s1", "2026-07-14", "1.09"])
            w.writerow(["s1", "2026-07-15", "1.10"])
        with open(d / "trusted_account_backtest_summary.csv", "w") as f:
            w = csv.writer(f)
            w.writerow(["strategy", "total_return", "max_drawdown",
                        "annualized_return", "trade_count", "buy_count",
                        "sell_count"])
            w.writerow(["s1", "0.10", "-0.20", "0.15", "11", "6", "5"])
    return d


def _scan_root(td: Path) -> Path:
    """构造 PROJECT_ROOT/exports/signal_research 扫描树并接管 PROJECT_ROOT"""
    root = td / "exports" / "signal_research"
    root.mkdir(parents=True)
    _scorer.PROJECT_ROOT = td
    return root


def test_new_style_dir_selected_when_latest(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = _scan_root(Path(td))
        old = _make_dir(root, "20260628_133147_production_all_strategy_review")
        _make_dir(root, "20260806_214345_492670_trusted_account_backtest")
        result = _scorer.find_latest_review_dir()
        assert result == str(root / "20260806_214345_492670_trusted_account_backtest")
        assert old.name not in result


def test_old_style_dir_selected_without_new_style(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = _scan_root(Path(td))
        old = _make_dir(root, "20260628_133147_production_all_strategy_review")
        result = _scorer.find_latest_review_dir()
        assert result == str(root / old.name)


def test_prefixed_csv_names_accepted():
    with tempfile.TemporaryDirectory() as td:
        d = _make_dir(Path(td), "x")
        # nav loads from the prefixed file without raising
        data = _scorer.load_nav_data(str(d))
        assert "s1" in data
        assert data["s1"].dates()[-1] == "2026-07-15"
        # full-history refs derive calmar and round trips from summary
        refs = _scorer.load_full_history_refs(str(d))
        assert "s1" in refs
        r = refs["s1"]
        assert r.total_return == 0.10
        assert r.trade_count == 5          # min(buy_count=6, sell_count=5)
        assert abs(r.calmar - 0.15 / 0.20) < 1e-9  # ann_ret / |mdd|


def test_missing_files_raise_for_nav_and_empty_refs():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "empty"
        d.mkdir()
        try:
            _scorer.load_nav_data(str(d))
            assert False, "expected FileNotFoundError"
        except FileNotFoundError:
            pass
        assert _scorer.load_full_history_refs(str(d)) == {}
