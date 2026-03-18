# Chenyiyun2087 项目优化清单

> 审查日期：2026-03-02
> 审查范围：全仓库代码 + 各模块 README + optimize.md 现状回看

---

## 一、总览

经过对项目所有模块（scoreRank、sina、eastmoney、chenyiyunSelected、backtest、web、scheduler、messager、scripts/ops）的系统性代码审查，整理出以下 6 大优化方向、共 **42 项** 具体优化项。

| 优化方向 | P0 (紧急) | P1 (重要) | P2 (改进) | P3 (锦上添花) |
|----------|-----------|-----------|-----------|---------------|
| 🔒 安全 | 3 | 1 | — | — |
| 🐛 正确性 | 2 | 2 | 1 | — |
| ⚡ 性能 | — | 4 | 3 | 1 |
| 🏗️ 架构 | — | 5 | 4 | 2 |
| 👁️ 可观测性 | — | 2 | 2 | — |
| 🧪 测试 | — | 1 | 3 | 2 |

---

## 二、P0 — 紧急修复（影响正确性或安全）

### 🔒 S-01：数据库密码硬编码在至少 15 处源码中

| 维度 | 说明 |
|------|------|
| **影响** | 密码 `19871019` 明文写入 Git；任何仓库只读权限即可获取生产凭据 |
| **涉及文件** | `scheduler.py`、`scoreRank/core/config.py`、`eastmoney/data_controller.py`、`sina/live_tracker/live_tracker_config.py`、`sina/bs_detection/SinaBSDetector.py`、`sina/bs_detection/main.py`、`sina/tools/check_bs_status.py`、`sina/config/config_*.json`、`web/app.py`、`scripts/ops/sync_trade_cal.py`、`scripts/ops/run_chenyiyun_daily.py` 等 |
| **建议方案** | 1) 创建 `.env` 文件（已加入 `.gitignore`）；2) 新建 `shared/db_config.py`，统一从 `os.environ` 读取；3) 全局搜索替换所有硬编码密码引用 |
| **工作量** | 1 天 |

### 🔒 S-02：Flask `secret_key` 为默认值

| 维度 | 说明 |
|------|------|
| **影响** | `web/app.py` 第 63 行 `app.secret_key = 'your_secret_key_here'`，Session cookie 可被伪造 |
| **建议** | 从环境变量读取，或使用 `os.urandom(24)` 生成 |
| **工作量** | 10 分钟 |

### 🔒 S-03：交易执行 API 无鉴权

| 维度 | 说明 |
|------|------|
| **影响** | `/api/live/execute_trade` 等路由无任何认证，局域网内任何人可下单 |
| **建议** | 至少添加 HTTP Basic Auth 或 API Token 校验；长期建议接入 Flask-Login |
| **工作量** | 0.5 天 |

### 🐛 B-01：`adj_type` 参数被忽略 — QFQ 与 Raw 查询返回相同数据

> 处理状态（2026-03-04）：✅ 已修复（`fetch_bars_batch` 已按 `adj_type` 动态选择 `adj_*` 或 `raw` 价格列）

| 维度 | 说明 |
|------|------|
| **影响** | `scoreRank/core/db_io.py` 第 78 行 `fetch_bars_batch()` 接受 `adj_type` 参数但 SQL 未使用。`TechnicalScorer` 调用两次（QFQ + Raw），实际获取相同数据 → **流动性评分基于错误数据计算** |
| **涉及文件** | `scoreRank/core/db_io.py`、`scoreRank/strategies/technical.py` |
| **建议** | 1) 在 SQL 中根据 `adj_type` 选取不同价格列（`adj_close` vs `close`）；2) 或使用不同表 |
| **工作量** | 0.5 天 |

### 🐛 B-02：`reconcile_account.py` 非事务性重建持仓

| 维度 | 说明 |
|------|------|
| **影响** | `scripts/ops/reconcile_account.py` 先 `DELETE FROM live_positions` 再逐条 INSERT，未包裹在事务中。中间崩溃会导致持仓数据完全丢失 |
| **建议** | 用 `BEGIN...COMMIT` 包裹，或先写临时表再 `RENAME TABLE` 原子替换 |
| **工作量** | 30 分钟 |

---

## 三、P1 — 重要优化（影响性能或可维护性）

### ⚡ P-01：`scorer.py` 中 9 次 `groupby().apply(lambda)` — 评分主瓶颈

> 处理状态（2026-03-04）：✅ 已修复（已改为 `groupby().transform()` 向量化路径）

| 维度 | 说明 |
|------|------|
| **影响** | `scoreRank/core/scorer.py` 第 43-65 行，对 ~5000 只股票 × 160 天数据做 9 次独立 groupby，每次遍历全量。是评分流水线最大性能瓶颈，估计可提速 **5-10 倍** |
| **建议** | 合并为单次 `groupby().apply()` 或使用 `transform()`，在一次遍历中计算所有滚动特征 |
| **工作量** | 1 天 |

### ⚡ P-02：`ClaudeScorer` 逐股票循环计算技术指标

> 处理状态（2026-03-04）：✅ 已修复（`_fetch_technical_momentum()` 已改为向量化计算，不再逐股票 Python 循环）

| 维度 | 说明 |
|------|------|
| **影响** | `scoreRank/strategies/claude.py` 第 100-178 行，`_fetch_technical_momentum()` 用 `for ts_code, group in df.groupby()` Python 循环计算 MACD/RSI/KDJ/CCI，O(N×T) 纯 Python 开销 |
| **建议** | 改为 `groupby().transform()` 向量化计算；或使用 `ta-lib` 库批量计算 |
| **工作量** | 1 天 |

### ⚡ P-03：SQL WHERE 中使用 `SUBSTR(ts_code, 1, 6)` 导致全表扫描

| 维度 | 说明 |
|------|------|
| **影响** | `scoreRank/core/db_io.py` 第 57、93 行对 `ts_code` 列做函数运算，MySQL 无法使用索引 |
| **建议** | 改为 `ts_code LIKE 'XXXXXX%'` 或新增虚拟列 `symbol` 并建索引 |
| **工作量** | 0.5 天 |

### ⚡ P-04：无数据库连接池 — 每次查询新建 TCP 连接

> 处理状态（2026-03-04）：✅ 已修复（`scoreRank/core/db_io.py`、`scoreRank/strategies/claude.py`、`sina/live_tracker/live_tracker_db.py` 已接入 SQLAlchemy 连接池复用）

| 维度 | 说明 |
|------|------|
| **影响** | `db_io.py`、`claude.py`、`live_tracker_db.py`（25+ 函数）均 open/close 连接。ClaudeScorer 单次运行 6 次查询 = 6 次 TCP 握手 |
| **涉及范围** | scoreRank、sina/live_tracker、eastmoney、chenyiyunSelected 四个模块 |
| **建议** | 使用 SQLAlchemy `create_engine(pool_size=5)` 或 `DBUtils.PooledDB` 全局连接池 |
| **工作量** | 1 天 |

### 🏗️ A-01：`web/app.py` 单文件 5,333 行 — 需拆分为 Flask Blueprints

| 维度 | 说明 |
|------|------|
| **影响** | 策略路由、Admin、API、Stock Pool CRUD、任务调度、通知分发、DB Schema Migration 全部混在一个文件中。协作困难、PR 冲突频繁 |
| **建议** | 拆分为：`web/routes/strategy.py`、`web/routes/admin.py`、`web/routes/api.py`、`web/scheduler.py`、`web/db_migration.py` |
| **工作量** | 2-3 天 |

### 🏗️ A-02：无共享基础设施层 — DB配置/工具函数重复定义 5+ 次

| 维度 | 说明 |
|------|------|
| **影响** | `DEFAULT_MYSQL_CONFIG`：5+ 处定义；DB URL 解析：3 种实现；`normalize_stock_codes()`：3 处定义；交易日判断：2 处定义 |
| **建议** | 创建 `shared/` 包：`shared/db_config.py`、`shared/stock_utils.py`、`shared/calendar.py` |
| **工作量** | 2 天 |

### 🏗️ A-03：批量脚本 import Flask App 全量模块 — 重耦合

| 维度 | 说明 |
|------|------|
| **影响** | `scripts/ops/run_m7_sell_eval.py` 导入 `web.app` 以使用 2 个私有函数，连带加载 Flask、模板、任务初始化等 |
| **建议** | 将被复用的逻辑（`_fetch_live_positions_snapshot`、`_sync_m7_sell_signals`）抽取到 `shared/` 或独立 service 层 |
| **工作量** | 0.5 天 |

### 🏗️ A-04：`scheduler.py` 与 `web/app.py` 双调度器并存

| 维度 | 说明 |
|------|------|
| **影响** | README 已说明 `scheduler.py` 不启用，但代码仍保留且有更新。容易混淆，已有人在两处修改调度逻辑 |
| **建议** | 明确废弃 `scheduler.py`（存档至 `archive/`），或反过来将 Web 调度独立出来使用 APScheduler |
| **工作量** | 0.5 天 |

### 🏗️ A-05：Schema 迁移代码散落在请求处理器中

| 维度 | 说明 |
|------|------|
| **影响** | `_ensure_task_management_schema()`、`_ensure_m7_sell_signal_table()` 等在每次请求时运行 `CREATE TABLE IF NOT EXISTS` + `DESC` + 潜在 `ALTER TABLE` |
| **建议** | 1) 独立为 `scripts/init_db.py` 启动时运行一次；2) 长期迁移用 Alembic |
| **工作量** | 1 天 |

### 🐛 B-03：涨停阈值硬编码为 9.5%/9.7% — 创业板(20%)与北交所(30%)错判

> 处理状态（2026-03-04）：✅ 已修复（已按板块规则动态判定：主板 10%、创业板/科创板 20%、北交所 30%）

| 维度 | 说明 |
|------|------|
| **影响** | `scorer.py` 第 97-98 行、`perf_utils.py` 第 52 行的涨停判断用固定阈值，对创业板/科创板/北交所股票会误判 |
| **建议** | 根据 `ts_code` 后缀（`.SZ 300xxx`→20%、`.BJ`→30%）动态确定板块涨停幅度 |
| **工作量** | 0.5 天 |

### 🐛 B-04：`sina_monitor` 页面四次重复的 `signal_stats` elif 分支

| 维度 | 说明 |
|------|------|
| **影响** | `web/app.py` 第 3220-3370 行，同一 elif 分支被复制粘贴了 4 次，只有第 1 次会执行，其余为死代码 |
| **建议** | 删除重复的 3 个分支 |
| **工作量** | 10 分钟 |

### 👁️ O-01：全局性缺失结构化日志 — 仅用 `print()` 输出

> 处理状态（2026-03-04）：🟡 部分修复（已在 `scoreRank` 核心链路接入结构化 JSON 日志与分级落盘；`web/app.py` 与 `sina/live_tracker/live_tracker.py` 仍需全量替换）

| 维度 | 说明 |
|------|------|
| **影响** | `scorer.py`、`db_io.py`、`claude.py`、`technical.py`、`live_tracker.py`、`app.py` 等核心模块均使用 `print()` 或完全无日志。生产环境错误无法追踪 |
| **建议** | 统一使用 Python `logging` 模块，配置 JSON 格式输出，分级别写入文件 |
| **工作量** | 1.5 天 |

### 👁️ O-02：调度器失败无通知 — 流水线静默失败

| 维度 | 说明 |
|------|------|
| **影响** | `scheduler.py` 的 `run_pipeline()` 失败后仅写日志，不调用 `messager/notification.py` 通知运维；`web/app.py` 仅在成功时通知 |
| **建议** | 在 `_execute_locked_task` 的异常/失败路径也调用通知分发，增加失败告警渠道 |
| **工作量** | 0.5 天 |

### 🧪 T-01：策略核心模块零单元测试覆盖

| 维度 | 说明 |
|------|------|
| **影响** | `scorer.py`（评分归一化函数）、`strategy_playbook.py`（M2-M7 策略）、`web/app.py`（路由逻辑）均无测试。`eastmoney/` 的 test 文件均为调试脚本而非 pytest 用例 |
| **建议** | 优先为 `_score_01_from_range`、`_score_01_centered`、M4 评分、M7 卖出规则引擎编写确定性输入/输出测试 |
| **工作量** | 3 天 |

---

## 四、P2 — 常规改进

### ⚡ P-05：M7 卖出信号在 GET 页面加载时写库

| 维度 | 说明 |
|------|------|
| **影响** | `web/app.py` 第 4501 行，每次打开 `/sina/strategy/m7` 页面都会触发 `_sync_m7_sell_signals()` 做 DELETE + INSERT |
| **建议** | 改为仅在 POST（手动触发）或任务调度时写库；GET 只读 |
| **工作量** | 0.5 天 |

### ⚡ P-06：`ClaudeScorer` 5 个顺序 SQL 查询可并行化

| 维度 | 说明 |
|------|------|
| **影响** | `claude.py` 第 56-60 行依次查询 6 张表，可用 `concurrent.futures.ThreadPoolExecutor` 并发 |
| **工作量** | 0.5 天 |

### ⚡ P-07：`sina_monitor` "latest_b" 全表关联子查询无日期边界

| 维度 | 说明 |
|------|------|
| **影响** | `web/app.py` 第 3050 行附近的 SQL 无日期限制，每次请求扫描 `bs_detection_results` 全表 |
| **建议** | 加 `WHERE batch_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)` 边界 |
| **工作量** | 15 分钟 |

### 🏗️ A-06：`SinaBSDetectorBase.py` — 5,943 行死代码

| 维度 | 说明 |
|------|------|
| **影响** | 是 `SinaBSDetector.py` 的旧版拷贝，无代码引用。增加仓库体积、混淆搜索结果 |
| **建议** | 删除或移至 `archive/` |
| **工作量** | 5 分钟 |

### 🏗️ A-07：`messager/notification.py` 2,894 行单文件

| 维度 | 说明 |
|------|------|
| **影响** | 包含所有通知渠道（微信、飞书、Telegram、邮件、Discord 等）+ 报告生成 + 格式化 |
| **建议** | 拆分为 `messager/channels/feishu.py`、`wechat.py` 等 + `messager/report_builder.py` |
| **工作量** | 2 天 |

### 🏗️ A-08：`sys.path` 手动操作 — 缺少顶层 `pyproject.toml`

| 维度 | 说明 |
|------|------|
| **影响** | 至少 5 个文件在头部做 `sys.path.insert(0, ...)` 以便跨模块 import |
| **建议** | 在项目根目录添加 `pyproject.toml`，`pip install -e .` 后所有模块可直接 import |
| **工作量** | 0.5 天 |

### 🏗️ A-09：`ClaudeScorer` 内重复实现 DB 连接逻辑

| 维度 | 说明 |
|------|------|
| **影响** | `claude.py` 的 `_query_df` 方法复制了 `db_io.py._fetch_rows` 的连接管理逻辑 |
| **建议** | 统一使用 `db_io` 模块，消除重复 |
| **工作量** | 0.5 天 |

### 👁️ O-03：Webhook 请求无超时设置

| 维度 | 说明 |
|------|------|
| **影响** | `notification.py` 中对飞书/企微/钉钉的 `requests.post()` 未设 `timeout`，可能无限挂起 |
| **建议** | 统一设 `timeout=(5, 15)` (connect, read) |
| **工作量** | 15 分钟 |

### 👁️ O-04：无运行指标采集

| 维度 | 说明 |
|------|------|
| **影响** | 无 Prometheus/StatsD 埋点。无法监控：评分耗时、Selenium 截图成功率、策略换手率 |
| **建议** | 短期：在关键路径记录执行时间到日志；长期：接入 Prometheus client |
| **工作量** | 2 天 |

### 🧪 T-02：`eastmoney/` 测试目录无有效测试用例

| 维度 | 说明 |
|------|------|
| **影响** | `test/Eastmoney/` 下均为调试脚本（`debug_eastmoney_api.py`、`verify_fix.py`），不是 pytest 测试 |
| **建议** | 将调试脚本重构为可自动化的 pytest 用例 |
| **工作量** | 1 天 |

### 🧪 T-03：无端到端流水线集成测试

| 维度 | 说明 |
|------|------|
| **影响** | 采集 → 评分 → M8 → 调仓 → 快照 全链路无集成测试。上游变更可能导致下游静默失败 |
| **建议** | 建立一套轻量级 smoke test，使用 mock 数据跑通完整流水线 |
| **工作量** | 3 天 |

### 🧪 T-04：无 CI/CD 流水线

| 维度 | 说明 |
|------|------|
| **影响** | 无 GitHub Actions / Jenkins 等自动化构建。代码提交后不会自动运行测试或检查 |
| **建议** | 添加 `.github/workflows/ci.yml`，至少做 `pytest backtest/tests` + `pytest test/ScoreRank/` |
| **工作量** | 0.5 天 |

---

## 五、P3 — 锦上添花

### 🏗️ A-10：Selenium 爬虫无重试机制

| 维度 | 说明 |
|------|------|
| **影响** | `BSpointChecker.py` 截图失败、`data_controller.py` 页面加载超时均直接跳过，无指数退避重试 |
| **建议** | 引入 `tenacity` 库或手写 3 次指数退避重试 |
| **工作量** | 1 天 |

### 🏗️ A-11：subprocess 编排缺少结构化错误回传

| 维度 | 说明 |
|------|------|
| **影响** | `scheduler.py` 通过 `subprocess.run()` 调用各脚本，仅获取退出码，无法获取具体错误信息 |
| **建议** | 替换为直接 Python 函数调用，或捕获 stderr 并传入通知系统 |
| **工作量** | 2 天 |

### ⚡ P-08：`strategy_playbook.py` M3 网格搜索在每次页面加载时执行

| 维度 | 说明 |
|------|------|
| **影响** | 58 组参数组合 × 策略计算 × 统计量计算，每次打开 `/sina/strategy/m3` 页面都重新计算 |
| **建议** | 增加结果缓存（Redis 或内存 LRU），仅在数据更新后失效 |
| **工作量** | 0.5 天 |

### 🧪 T-05：`scorer.py` 归一化函数缺少边界测试

| 维度 | 说明 |
|------|------|
| **影响** | `_score_01_from_range(val, lo, hi)` 在 `hi <= lo`、`val=NaN`、`val=inf` 场景无测试覆盖 |
| **建议** | 补充参数化测试用例 |
| **工作量** | 2 小时 |

### 🧪 T-06：回测引擎缺少多频率/多资产支持

| 维度 | 说明 |
|------|------|
| **影响** | `backtest/` README 已列为待改进项。当前仅支持日频单品种回测 |
| **建议** | 扩展 `clock.py` 支持 15min/60min 频率，`portfolio.py` 支持多策略混合 |
| **工作量** | 5 天 |

---

## 六、M7 调仓优化（基于 optimize.md 现状回看）

以下为 `optimize.md` 中标注为 ⚠️/⏳ 的遗留项：

| 编号 | 状态 | 项目 | 说明 |
|------|------|------|------|
| M7-01 | ⚠️ | 买入取整逻辑 | 当前为 `round(.../100)*100`，应为 `math.floor(.../100)*100`（向下取整），避免超买 |
| M7-02 | ⚠️ | 普通买入未使用 `min_trade_notional` | 仅有权重门槛，缺少最小金额门槛 |
| M7-03 | ⚠️ | 默认参数与方案建议值不一致 | 8 个参数的实际默认值与 optimize.md 建议值有差异（如 `stop_loss_pct` 方案建议 7% 实际 6%） |
| M7-04 | ⏳ | 缺少独立开关 | `enable_trailing_stop`、`enable_time_stop`、`enable_score_exit` 无独立开关，只能通过极端参数值间接禁用 |
| M7-05 | ⏳ | 参数回测与阈值固化 | 阶段二已上线核心能力，但"参数回测结论沉淀"尚未完成 |
| M7-06 | ⏳ | 验收指标统计功能 | 强制卖出占比、平均持仓天数、误杀率等周度评估指标尚未实现 |

---

## 七、建议实施路线

```
第 1 周（守底线）
├── S-01: 全局密码提取到 .env + shared/db_config.py
├── S-02: Flask secret_key 修复
├── S-03: 交易 API 增加 Basic Auth
├── B-01: 修复 adj_type 语义 bug
├── B-02: reconcile_account 事务包裹
└── B-04: 删除四重复 elif 分支

第 2 周（提性能）
├── P-01: scorer.py groupby 合并
├── P-02: ClaudeScorer 向量化
├── P-03: SQL SUBSTR → LIKE
├── P-04: 统一连接池
└── O-01: 全局 logging 替换 print()

第 3-4 周（优架构）
├── A-01: web/app.py Blueprint 拆分
├── A-02: 创建 shared/ 公共包
├── A-06: 删除 SinaBSDetectorBase.py 死代码
├── A-08: 添加 pyproject.toml
├── O-02: 失败通知集成
└── T-01: 策略核心函数单元测试

第 5+ 周（持续改进）
├── M7 遗留项（M7-01 ~ M7-06）
├── 集成 CI/CD
├── notification.py 拆分
└── 回测引擎增强
```

---

## 八、附录 — 各模块已有待改进项汇总（来自各 README）

| 模块 | README 中的待改进项 | 本文对应编号 |
|------|---------------------|-------------|
| scoreRank | 统一不同策略输出字段 | A-09 |
| scoreRank | 关键阈值参数版本化落盘 | M7-05 |
| scoreRank | 增加无数据库 smoke 命令 | T-04 |
| chenyiyunSelected | strategy/research 接口文档化 | — (新增建议) |
| chenyiyunSelected | 回测结果与信号共用 schema | — (新增建议) |
| chenyiyunSelected | 补充最小回归样例数据 | T-01 |
| backtest | 扩展交易成本模型 | T-06 |
| backtest | 多频率时钟支持 | T-06 |
| backtest | 标准化报告模板 | — (新增建议) |
| eastmoney | 结果文件命名增加时间戳 | — |
| eastmoney | 数据抓取分级重试 | A-10 |
| eastmoney | 策略参数与报告绑定 | M7-05 |
