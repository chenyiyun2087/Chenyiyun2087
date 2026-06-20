# Strict ledger v2 版本修复

## 根因

`integration/strict-ledger-full-history` 在与主线同步时保留了旧的冲突侧，导致其 tree 回退了原子公司行为、逐日 lifecycle、72-run 三策略矩阵和证据必需项。不得将该分支或其 merge commit 作为后续研究基线。

## v2 基线与操作顺序

- `integration/strict-ledger-full-history-v2` 直接从 `origin/main` 建立；`origin/main` 是包含 strict 安全实现的唯一基线。
- `preflight24` 以 development/holdout 各自结束日前 60 个 lifecycle 交易日组成两个窗口，运行两组耦合成本/滑点情景和 `no_cap`/`strict_cap`；结果必须是 8 个三策略 run、24 条策略级结果。
- `full216` 固定为 72 个三策略 run、216 条策略级结果。所有 run 均将 backtest 和审计结果写入矩阵 cell 自己的目录。
- 仅在 GitHub 对 v2 单提交 review 通过后运行 `preflight24`；24-cell 通过后才可运行 `full216`。

## 准入边界

`promotion_enabled` 永远为 `false`。即使矩阵可靠性阈值全部满足，唯一许可状态也是 `RESEARCH_ELIGIBLE_FOR_DISABLED_SHADOW`；production v1 与 `research_shadow_candidate.enabled=false` 不在本次变更范围内。
