# 运行手册

本文档记录项目主链路和常用操作入口。更详细的生产候选导出步骤见 `docs/production_trusted_strategy_usage.md`。

生产自动任务的完整时间表、依赖顺序、消息摘要和排障入口见[《系统批量任务与消息推送运维总览》](BATCH_TASK_OPERATIONS.md)。

## 1. Sina 信号主线

```text
sina_picture 抓图
    -> sina_analyse 识别买卖点
    -> bs_detection_results
    -> scoreRank.run_daily 评分
    -> score_rank_daily
    -> M2~M8 回归 / M7 调仓 / Web 展示
```

## 2. ScoreRank 评分主线

```text
B/S 股票池 + 自选股 + 全市场股票
    -> 拉取行情数据
    -> Technical 技术评分
    -> Claude 六维评分
    -> opt_score 因子优化分
    -> 写入 score_rank_daily
    -> Web 展示 / M8 回归 / M7 调仓
```

当前评分体系不是单一分数：

| 字段 | 含义 |
|---|---|
| `score` | 技术总分。 |
| `opt_score` | 因子优化分。 |
| `claude_score` | AI 六维分。 |
| `s_liquidity` | 流动性分。 |
| `bs_score_v2` | 规则类 B 点增强分。 |
| `bs_consensus_score` | B 点综合分。历史回测中需确认不含未来模型字段。 |

## 3. M2~M8 策略链

| 阶段 | 作用 |
|---|---|
| M2 | 固定策略预设回归。 |
| M3 | 参数网格搜索。 |
| M4 | 多策略投票融合，形成组合分配。 |
| M5 | 滚动验证。 |
| M6 | 净值回测。 |
| M7 | 模拟调仓生成。 |
| M8 | 周期任务，回归优化并落库。 |

## 4. 可信全量池生产研究链

```text
score_rank_daily 完整性校验
    -> 可信策略账户级 T+1 回测
    -> 生产候选 Top5 导出
    -> 本地订单草案
    -> 飞书通知
    -> 影子盘成交监控
```

当前生产候选导出仍以 `docs/production_trusted_strategy_usage.md` 为准。生产默认风险档为 `adaptive`，主策略为 `baseline_full_liquidity_detail_vol_position`；`adaptive_market_style` v2.2 保留为风控锚和仓位治理参照。旧研究策略 `adaptive_style_switch` 仅保留为历史回测对照。

## 5. 常用命令

```bash
# 只使用轮换后的非特权只读账户；不要在命令行或仓库中写口令
export CHENYIYUN_DB_URL='由安全凭据存储注入的完整只读URL'
export CHENYIYUN_DB_READ_ONLY=1

# 评分
python3 -m scoreRank.cli.run_daily --date YYYY-MM-DD --force

# 行业回填
python3 scripts/backfill_score_rank_daily_industry.py --execute

# 账户级可信策略回测
python3 scripts/research/run_production_governed_vol_position_backtest.py \
  --start-date 2025-06-03 \
  --end-date 2026-05-29 \
  --initial-cash 500000 \
  --top-n 5 \
  --hold-days 10 \
  --max-total-positions 5 \
  --trade-cost-rate 0.00075

# 统一赛马预检：无需数据库，不产生晋级结论
python3 scripts/research/run_production_strategy_tournament.py \
  --as-of YYYY-MM-DD \
  --output-dir exports/strategy_tournament/precheck_YYYYMMDD \
  --precheck-only

# 正式赛马：工作区必须 clean，每个策略必须显式绑定同身份证据目录
python3 scripts/research/run_production_strategy_tournament.py \
  --as-of YYYY-MM-DD \
  --output-dir exports/strategy_tournament/formal_YYYYMMDD \
  --source production_governed_vol_position=/absolute/evidence/P0 \
  --source full_strategy_v3=/absolute/evidence/A9
```

## 6. 未来函数红线

- 信号日 T 只能使用 T 日已落库评分和 T 日及以前行情。
- T+1 开盘成交，不能用 T+1 之后价格决定 T 日候选。
- 动态因子和策略切换只能使用 `exit_date < T` 的已完成样本。
- `bs_model_*` 历史回填字段默认不参与可信回测。

## 7. Web 调度器常驻与健康检查

数据库凭据放在用户级文件 `~/.config/chenyiyun/web.env`，权限必须为 `600`；不要把凭据写入仓库或 launchd plist。

```bash
chmod 600 ~/.config/chenyiyun/web.env
scripts/ops/install_web_launchd.sh
scripts/ops/check_web_console.sh
```

项目位于外置卷时，macOS 必须允许 `/bin/bash` 和项目使用的 Python 访问“可移除卷”；若 launchd 日志出现 `Operation not permitted`，先在“系统设置 → 隐私与安全性”授予权限，再重新运行安装脚本。日志位于 `~/Library/Logs/Chenyiyun2087/`。

### 批量 Worker 租约与隔离发版（2026-10-17）

Worker 在每次领取任务前、任务心跳及子进程结束后，使用原连接核验 `GET_LOCK` 所有权，禁止自动重连冒充原租约。丢失连接或所有权时停止正在执行的任务进程组，记录失败并退出，由 launchd 重启后重新申请租约。健康检查同时检查数据库租约，不能仅据 launchd 的 running 状态认定正常。没有新增租约宽限开关。

从隔离工作区发版时，使用 `deploy_production_release.py --source-root <clean-checkout> --runtime-source-root /Volumes/extension/projects/Chenyiyun2087 --runtime-python /Volumes/extension/projects/Chenyiyun2087/.venv/bin/python --release-id <id> --stability-hotfix`。`--runtime-source-root` 指定持久数据源和 manifest 的 source_repo；默认仍为 source-root。生成目录链接解析到持久实际目录，发布前再次校验目标代码目录洁净，禁止直接修改已发布 release 的脚本。

扫描历史股票资格使用 `GROUP BY ts_code HAVING MIN(trade_date) <= :target_date`，利用现有 `idx_ts_date` 读取首个交易日；保留目标日尚未上市股票的排除和停牌股票的资格。无需增加索引或改库结构。

回滚本轮稳定性修复可切回前一 release manifest 并重新安装服务；旧版缺少租约持续核验，应先核实数据库锁与代码洁净。不要随代码回滚删除已补录数据或原始失败记录。
