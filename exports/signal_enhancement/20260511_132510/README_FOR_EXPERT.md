# Sina B点信号增强数据包

## 目标

请基于“B点首次出现时，当时已知的信息”增强买点后的排序/过滤信号。建议目标不是预测所有股票涨跌，而是在已经出现 B 点的候选中，提高：

- 未来 10/20 个交易日最大涨幅命中率，例如 `hit_10_10pct`、`hit_20_10pct`
- 收益回撤比，例如 `max_ret_20` 与 `mdd_20`
- 最新候选的可交易排序

## 文件

- `first_buy_events_labeled.csv`：主训练表，一行代表某股票某日首次出现 B 点。包含当时评分、信号描述、未来 1/3/5/10/20/60 日收益标签。
- `first_buy_price_paths_60d.csv`：首次 B 点后最多 60 个交易日的相对收益路径，`rel_ret_d0=0`。
- `active_b_daily_panel_labeled.csv`：辅助表，一行代表某股票在某日仍处于 B 点有效状态，适合研究持有期加减仓。
- `latest_b_candidates.csv`：最新交易日仍有效的 B 点候选，仅用于专家产出排序/打分，无未来标签。
- `signal_enhancement_dataset.xlsx`：同内容 Excel 汇总版。
- `DATA_DICTIONARY.md`：字段解释。
- `feature_whitelist.json`：允许进入模型的特征白名单，已排除未来标签。
- `quality_report.json`：标签完整性、split 分布、缺失率摘要。
- `FIELD_CONTRACT.json`：数据包字段分组契约，标记身份字段、原子特征、模型输出、市场上下文、标签等字段是否齐备。
- `split_protocol.json`：horizon-aware walk-forward + embargo 切分协议。
- `summary.json`：本次导出的统计摘要。

## 防泄漏约束

训练新信号时只能使用 `ret_*`、`max_ret_*`、`mdd_*`、`hit_*`、`days_to_*` 以外的字段作为特征。所有未来收益字段只能作为标签或评估指标。

## 本次样本规模

- 首次 B 点事件：905 行
- 带 10 日标签的首次 B 点事件：732 行
- B 点有效状态日切片：8521 行
- 最新候选：242 行
- 数据日期范围：2026-01-22 至 2026-05-08

## 建议交付物

请专家返回：

- 新评分公式或模型说明
- 每个候选的增强分，最好 0-100
- 分层阈值建议：强买/观察/剔除
- 在 train/validation/test 三段上的命中率、平均最大涨幅、平均最大回撤
