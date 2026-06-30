# 双项目策略晋级与阻断决定

## Executive Summary

- **`CANARY_ELIGIBLE`：0。** 没有策略同时具备研究、账户和执行三类闭环证据。
- **`SHADOW_ONLY`：0。** 当前影子标签或影子评分都没有达到“研究已通过、仅账户或市场覆盖不足”的门槛。
- **`BLOCKED`：6 个统一策略实体。** Cheny 的六个目标策略因新鲜严格回放与双账本不可验证而阻断；其中三者还有严重回撤或集中度问题。
- **其余均为 `RESEARCH_ONLY`。** 这不是正面评价，只表示证据仍停留在研究/发现层或严重缺失，不能进入资金验证。

## 决定

### BLOCKED

- `AUTO↔ashare_auto_shadow`
- `production_governed_vol_position`
- `baseline_full_liquidity_detail_vol_position`
- `adaptive_market_style`
- `dual_system_adaptive_route`
- `tiered_liquidity_then_bs_v2`

共同阻断项：Cheny 权威公司行为、证券生命周期和交易日历快照缺失；新鲜严格回放未执行；第二套独立账户回放账本不存在；逐日/逐单差异不能计算。额外风险详见风险排序。

### RESEARCH_ONLY

- 候选语义映射：`hybrid_conservative_v1↔ashare_hybrid_conservative_shadow`、`trend_breakout_v1↔ashare_trend_breakout_shadow`。
- 有局部研究证据：`plate_enhanced_v3_v8_locked`、`plate_enhanced_v3`、`market_regime_timing_formal`、`lgbm_shadow_model`。
- 无足够策略级验证：`adaptive_vol_position_v1`、`classic`、`hybrid_conservative_v2`、`pullback_quality_v1`、`monitor_explain_v1`、`chenyiyun_selected`、`repair_reversal_shadow`。
- 仅组件/信号身份：`quant_selection_engine`、`market_regime_gate`、`plate_enhanced_v3_env`、`bs_signal_trigger`、`turtle_breakout`、`volume_breakout`、`ma20_pullback`、`ma250_retest`、`low_drawdown_uptrend`、`multi_indicator_resonance`、`tech_score_ranker`、`factor_alpha_ranker`、`adaptive_vol_ranker`、`smart_money_resonance`、`risk_veto_basic`。

## 必答结论

### 哪些策略只是“研究表现好”？

**没有。** ADC 中证据最丰富的 `plate_enhanced_v3_v8_locked` 六个活跃年度全部为负；`market_regime_timing_formal` 是 9 日 fixture-like 证据；`plate_enhanced_v3` 是零交易 smoke；`lgbm_shadow_model` 成本后 IR 为 -1.749。不能把“有研究工件”改写为“研究表现好”。

### 哪些策略只是“账户账本看起来好”？

若“看起来好”仅指不可复现的历史保存账户总收益为正，则有：

- `baseline_full_liquidity_detail_vol_position`：全历史 +18.62%，但最大回撤 -66.41%。
- `adaptive_market_style`：全历史 +42.61%，但最大回撤 -37.33%。
- `dual_system_adaptive_route`：61 日 +4.21%，样本不足。
- `ashare_auto_shadow`：61 日 +8.72%，但接近单一股票/行业满仓。

这些都不是已验证的账户证据，不能用于晋级。`tiered_liquidity_then_bs_v2` 全历史 -71.33%，`ashare_hybrid_conservative_shadow` 61 日 -11.82%，不属于“看起来好”。

### 哪些策略同时拥有研究与执行证据？

**没有。** 三个名称相近的跨项目候选映射都未证明同源、共享信号或共享特征；Cheny 的历史保存账户结果也没有新鲜回放和双账本一致性。

### 最大风险分别来自哪里？

- 数据：所有策略；特别是 `plate_enhanced_v3_v8_locked` 与全部 Cheny 策略。
- 过拟合/夹具：`market_regime_timing_formal`。
- 回撤：`tiered_liquidity_then_bs_v2`、`baseline_full_liquidity_detail_vol_position`、`adaptive_market_style`。
- 容量：`plate_enhanced_v3_v8_locked` 已有直接压力证据；其余不可验证。
- 执行：全部 Cheny 策略，尤其六个 BLOCKED 实体。

## 晋级纪律

在共同策略版本、冻结数据快照、同一成本/成交/持仓合同、精确候选到订单关联、两套独立账本逐日逐单一致、跨市场状态 OOS/WF 和容量压力测试同时成立前，不得进入灰度资金验证。
