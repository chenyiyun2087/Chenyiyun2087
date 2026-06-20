# ADR-0003: web/app.py 是唯一生产调度入口

## 状态
已接受（Accepted）

## 日期
2026-06-21

## 背景
项目存在两个调度器：
- `web/app.py`：Flask 内置任务系统，通过 `TASKS` 字典定义所有生产任务，使用 MySQL 行级锁实现跨进程幂等。
- `scheduler.py`：独立的 Python 调度脚本，曾用于 21:00 日终管线，现已不作为生产调度器。

## 决定
1. `web/app.py` 是**唯一的生产调度入口**。所有定时任务必须在此定义。
2. `scheduler.py` 作为**历史参考和开发便利工具保留**，但不得用于生产环境。
3. 任何新增的定时任务必须：
   - 在 `web/app.py` 的 `TASKS` 字典中注册。
   - 具有幂等键（task_name + business_date）。
   - 在 `app_task_history` 中记录执行结果。
4. 任务执行必须可审计：trigger_type、started_at、finished_at、status、exit_code。

## 后果
- **正向**：单一调度入口，消除双写和调度冲突风险。
- **负向**：scheduler.py 与 web/app.py 的任务定义可能不同步，需要手动对齐。
- **合规**：生产事故复盘只以 web/app.py 的执行记录为准。
