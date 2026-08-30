# 批量任务修复与重跑验收报告

- 执行日期：2026-08-20（Asia/Shanghai）
- 范围：`chenyiyun`、`tushare_stock`
- 目标交易日：20260814、20260817、20260818、20260819

## 结果

### chenyiyun

- 83 个队列任务全部为 `SUCCESS`：20260814 为 23 个，其余三个交易日各 20 个。
- 当前没有 `FAILED`、`BLOCKED`、`RUNNING` 或 `PENDING` 任务。
- 重跑并通过的链路包括：Signal Package、K 线诊断、Signal Precommit、Sell Precommit、每日批量审计。
- 每日审计均为 `rows=21`、`replay_required=0`。
- K 线覆盖校验通过：
  - 20260817：5538 / 5538，北交所 335 / 335；
  - 20260818：5539 / 5539，北交所 335 / 336；
  - 20260819：5539 / 5539，北交所 335 / 336。

### tushare_stock

- `akshare_stock_spot` 20260817、20260818、20260819 均为 `READY`，覆盖率和质量分均为 1.0，实际行数分别为 5539、5540、5541。
- `dws_0830_targets` 对历史失败日 20100407 已补跑并记录为 `SUCCESS`。
- DWD 标准行情最新日期为 20260819；目标日主键无重复：5538、5539、5539 行均为不同股票。
- 旧的 HTML/JSON 解码失败、DWS 20100407 缺失来源失败记录保留在历史日志中，未删除；其后的成功重跑记录已写入。

## 修复内容

- 修复 `chenyiyun` K 线验证器：诊断表使用 ISO 日期，DWD 标准行情使用 `YYYYMMDD` 整数，并以目标日实际行情作为历史覆盖基准。
- 在 AkShare 股票现货同步中增加 Sina、东方财富和同日 DWD 行情回退链路，避免上游 HTML/断连响应造成整批失败。
- 重载任务 worker，并按依赖顺序重跑受影响任务。

## 校验

- `python3 -m py_compile`：通过。
- `git diff --check`：通过。
- `AShareDataCenter/tests/test_akshare_source_sync.py`：15 passed。

## 变更位置

- `web/app.py` 的修复已提交于主仓库提交 `9752c13a`。
- AkShare 同步修复位于 `/Volumes/extension/projects/AShareDataCenter/data_enhancement/akshare_source_sync/service.py`；该外部仓库原本存在其他未提交改动，本次未覆盖或清理这些改动。
