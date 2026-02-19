# scoreRank 模块说明

`scoreRank/` 提供多模型评分能力，是策略候选股排序与分层的核心模块。

## 组件回顾

- `core/`
  - `scorer.py`：评分执行主逻辑。
  - `perf_utils.py`：性能与统计辅助。
  - `db_io.py`：数据库读写。
  - `config.py`：运行配置。
- `strategies/`
  - `technical.py` / `fama.py` / `claude.py`：不同打分维度实现。
  - `base.py`：策略抽象基类。
- `cli/`
  - `run_daily.py`：日频执行入口。
  - `run_m8_cycle.py`：M8 周期任务。
  - `build_b_event_kpi.py`：B 事件 KPI 构建。

## 推荐入口

```bash
python -m scoreRank.cli.run_daily
```

## 测试

仓库内提供 `test/ScoreRank/` 系列功能测试与回归测试，可用于策略调整后的验收。

## 可改进点（待统一实施）

- 统一不同策略输出字段，减少后处理分支。
- 将关键阈值参数版本化并落盘。
- 增加无数据库模式下的 smoke 命令用于 CI。
