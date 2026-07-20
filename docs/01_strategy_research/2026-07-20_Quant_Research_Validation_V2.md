# Quant Research Validation V2 方法冻结说明

本次升级不增加新因子、不调整生产 Alpha 权重，也不把近期冠军直接路由到生产。研究型分配器固定为 50% Frozen Champion、30% 市场状态匹配策略、20% Challenger Shadow；任何不合格袖套的预算留在现金。

Nested Walk-forward 的参数信息流固定为 Train 估计、Validation 选择、Test 只评价。Test 数据变化不得改变冻结模型身份；Validation 数据变化可以改变冠军，但必须产生新的选择审计、`factor_model_id`、参数集合、随机种子与配置 SHA。

全部正式结果必须使用 2013 年至最新完整交易日、T 日信号/T+1 开盘、历史股票池与 PIT 公司行为/分类。当前缺少真实只读数据和双证据卷，故没有新增正式收益、回撤或扩资结论，状态继续 `BLOCKED / NO_SCALE`。
