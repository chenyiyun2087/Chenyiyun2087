"""策略注册器 — 统一读取 strategy_cards/ 下的 YAML 策略卡。

职责：
  1. 加载所有策略卡，验证必填字段
  2. 根据策略状态（PRODUCTION/SHADOW/LEGACY 等）执行门禁检查
  3. 提供策略身份查询接口（供候选导出、订单生成、风险审批使用）

使用：
  from strategy_registry import load_strategies, get_production_strategies, status_gate
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from runtime.release_registry import load_release_registry

logger = logging.getLogger(__name__)

STRATEGY_CARDS_DIR = Path(__file__).resolve().parent / "strategy_cards"

# 策略状态枚举
VALID_STATUSES = {"RESEARCH", "SHADOW", "PAPER", "CANARY", "PRODUCTION", "LEGACY", "RETIRED"}

# 禁止生成订单的状态
ORDER_BLOCKED_STATUSES = {"LEGACY", "RETIRED", "RESEARCH"}

# 可进入影子盘的状态
SHADOW_ALLOWED_STATUSES = {"SHADOW", "PAPER", "CANARY", "PRODUCTION"}


@dataclass
class StrategyCard:
    strategy_id: str
    strategy_version: str
    release_id: str
    owner: str
    status: str
    description: str = ""
    file_path: Path | None = None

    # 可选字段
    max_positions: int = 5
    holding_days: int = 10
    target_exposure: float = 0.70

    # 来源
    raw: dict = field(default_factory=dict)
    candidate_pool: str = ""
    allowed_regimes: tuple[str, ...] = ()
    pool_role: str = ""
    max_budget_share: float = 0.0

    @property
    def is_production(self) -> bool:
        return self.status == "PRODUCTION"

    @property
    def is_shadow(self) -> bool:
        return self.status == "SHADOW"

    @property
    def can_generate_orders(self) -> bool:
        return self.status not in ORDER_BLOCKED_STATUSES

    @property
    def can_run_shadow(self) -> bool:
        return self.status in SHADOW_ALLOWED_STATUSES


def load_all_cards() -> dict[str, StrategyCard]:
    """加载 strategy_cards/ 下所有 YAML 策略卡。"""
    cards: dict[str, StrategyCard] = {}
    if not STRATEGY_CARDS_DIR.exists():
        logger.warning("strategy_cards/ directory not found")
        return cards

    release_registry = load_release_registry()
    for yaml_file in sorted(STRATEGY_CARDS_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.error(f"Failed to parse {yaml_file}: {e}")
            continue

        sid = raw.get("strategy_id")
        if not sid:
            logger.error(f"Missing strategy_id in {yaml_file}")
            continue

        release = release_registry.releases.get(sid)
        if release is None:
            logger.error(f"Strategy '{sid}' is missing from strategy_release_registry.yaml")
            continue
        status = release.lifecycle_status
        if status not in VALID_STATUSES:
            logger.error(f"Invalid status '{status}' in {yaml_file}")
            continue

        cards[sid] = StrategyCard(
            strategy_id=sid,
            strategy_version=release.strategy_version,
            release_id=release.release_id,
            owner=str(raw.get("owner", "")),
            status=status,
            description=str(raw.get("description", "")),
            file_path=yaml_file,
            max_positions=int(raw.get("max_positions", 5)),
            holding_days=int(raw.get("holding_days", 10)),
            target_exposure=float(
                raw.get("risk_budget", {}).get("target_exposure", 0.70)
            ),
            candidate_pool=str(raw.get("candidate_pool") or raw.get("pool", {}).get("candidate_pool", "")),
            allowed_regimes=tuple(str(item) for item in raw.get("allowed_regimes", raw.get("pool", {}).get("allowed_regimes", []))),
            pool_role=str(raw.get("pool_role") or raw.get("pool", {}).get("role", "")),
            max_budget_share=float(raw.get("max_budget_share", raw.get("risk_budget", {}).get("max_budget_share", 0.0))),
            raw=raw,
        )

    prod_count = sum(1 for c in cards.values() if c.is_production)
    if prod_count == 0:
        logger.warning("No PRODUCTION strategy found in strategy_cards/")
    if prod_count > 1:
        logger.warning(f"Multiple PRODUCTION strategies ({prod_count}) — ensure only one is active")

    return cards


def get_production_strategies() -> list[StrategyCard]:
    """获取所有 PRODUCTION 状态的策略。"""
    return [c for c in load_all_cards().values() if c.is_production]


def get_shadow_strategies() -> list[StrategyCard]:
    """获取所有 SHADOW 状态的策略。"""
    return [c for c in load_all_cards().values() if c.status == "SHADOW"]


def status_gate(strategy_id: str, action: str = "generate_orders") -> tuple[bool, str]:
    """策略状态门禁。

    Returns:
        (allowed, reason)
    """
    cards = load_all_cards()
    card = cards.get(strategy_id)
    if card is None:
        return False, f"Strategy '{strategy_id}' not found in registry"

    if action == "generate_orders":
        if not card.can_generate_orders:
            return False, f"Strategy '{strategy_id}' is {card.status} — order generation blocked"
        return True, f"Strategy '{strategy_id}' is {card.status} — order generation allowed"

    if action == "run_shadow":
        if not card.can_run_shadow:
            return False, f"Strategy '{strategy_id}' is {card.status} — shadow execution blocked"
        return True, f"Strategy '{strategy_id}' is {card.status} — shadow execution allowed"

    if action == "promote":
        if card.status in ("LEGACY", "RETIRED"):
            return False, f"Strategy '{strategy_id}' is {card.status} — cannot promote"
        return True, f"Strategy '{strategy_id}' is {card.status} — eligible for promotion review"

    return True, f"Strategy '{strategy_id}' passed gate for action '{action}'"
