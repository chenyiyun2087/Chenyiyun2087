#!/usr/bin/env python3
"""Generate/check CURRENT_PRODUCTION_STATE.md from frozen production sources.

Run: python scripts/maintenance/generate_current_production_state.py [--check]
Needs: config registry, production config, and release freeze JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/00_project_overview/CURRENT_PRODUCTION_STATE.md"


def render() -> str:
    registry = yaml.safe_load((ROOT / "config/strategy_release_registry.yaml").read_text(encoding="utf-8"))
    production = yaml.safe_load((ROOT / "config/production_strategy.yaml").read_text(encoding="utf-8"))["production"]
    release_id = str(registry["active_production_release_id"])
    freeze_path = ROOT / "config/release_freeze" / f"{release_id}.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    release = registry["releases"]["production_governed_vol_position"]
    if release["release_id"] != release_id or freeze["release_id"] != release_id:
        raise ValueError("production_release_sources_disagree")
    if int(release["approved_principal"]) != int(freeze["approved_principal"]):
        raise ValueError("approved_principal_sources_disagree")
    if float(production["position_ratio"]) != float(freeze["max_nav_exposure"]):
        raise ValueError("production_exposure_sources_disagree")
    return f"""# Current Production State

> 此文件由 `scripts/maintenance/generate_current_production_state.py` 确定性生成；禁止手工维护。

| 字段 | 当前值 |
|---|---|
| 生产发布 | `{release_id}` |
| 固定资本策略 | `{release['strategy_id']}` |
| 选择引擎 | `{production['primary_selection_strategy']}` |
| 生命周期 | `{release['lifecycle_status']}` |
| 本金例外 | ¥{int(freeze['approved_principal']):,}（仅存量） |
| 目标仓位上限 | {float(freeze['max_nav_exposure']):.0%} |
| 执行 | `{production['execution_mode']}` / `{release['order_policy']}` |
| 扩资状态 | `NO_SCALE` |
| 新增资本 | ¥0 |
| 风险暴露增加 | 禁止 |
| 外部资本 | 禁止 |
| Broker API | 禁止 |

Smart Beta 与 Pure Alpha 均为隔离的 T21:30 研究身份；未取得路径绑定的正式 E3、正式前向经济门槛和人工审批前，不得晋级或分配资本。

来源：`config/strategy_release_registry.yaml`、`config/production_strategy.yaml`、`config/release_freeze/{release_id}.json`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("CURRENT_PRODUCTION_STATE.md drifted; regenerate it")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
