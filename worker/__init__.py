"""Worker — 任务执行器。

从 app_task_queue 消费任务，调用 domain service 执行，写入 audit log。
独立于 web/app.py 的 Flask 进程，可单独部署。
"""
