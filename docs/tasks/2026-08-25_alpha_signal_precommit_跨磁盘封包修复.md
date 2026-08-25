# alpha_signal_precommit 跨磁盘封包故障修复

## 现象

2026-08-25 的 `alpha_signal_precommit` 队列 #4319 失败：

```text
shadow_blocked: no SEALED package for execution_date 2026-08-25
```

这不是策略信号本身失败，而是前一天的 `alpha_signal_package_seal` 没有把包成功发布到可见的 SEALED 目录。

## 根因

生产任务运行在 detached release checkout（系统盘），而 release 部署逻辑把
`exports/forward_shadow_evidence/packages/.staging` 链接到了源仓库（项目盘）。
封包使用 `os.rename(staging, package)` 做原子发布时跨文件系统，触发：

```text
[Errno 18] Cross-device link
```

因此封包任务 #4305 失败，次日跨日消费者 #4319 才看到“没有 SEALED 包”的二次错误。旧调度器又只检查同一业务日期的依赖，无法把这个跨日等待关系表达出来。

## 修复

- 封包、影子执行、Web verifier 统一使用 `CHENYIYUN_SOURCE_REPO` 指向的持久 evidence 根目录，不再把 evidence 写进会轮换的 release checkout。
- 封包 staging 强制落在目标包同一文件系统；发现软链接或跨设备 staging 时，自动回退到目标父目录下的隐藏临时目录，保证原子 rename 不触发 `EXDEV`。
- release 共享逻辑跳过所有隐藏目录，特别是 `packages/.staging`，避免再次生成跨设备 staging 软链接。
- `alpha_signal_precommit` 在定时队列中等待匹配 `execution_date` 的 SEALED 包；`alpha_signal_execution_reconcile` 增加同日 precommit 依赖。
- 封包命令输出路径按持久 evidence 根目录计算；已有有效 SEALED 包的重试保持幂等，不覆盖原包、不创建 revision。
- 默认生产 release 根目录切换到项目盘 `/Volumes/extension/projects/chenyiyun-production-releases`，避免历史 release worktree 挤满系统盘。旧 release 未删除。

## 修复后补跑

| 队列 | 结果 | 结果摘要 |
|---|---|---|
| #4305 `alpha_signal_package_seal`（2026-08-24） | SUCCESS | `revision=1`，manifest 已落在持久 evidence 根目录 |
| #4309 `alpha_signal_sell_precommit`（2026-08-24） | SUCCESS | `execution_date=2026-08-25`，无可卖持仓，`skipped=62` |
| #4319 `alpha_signal_precommit`（2026-08-25） | SUCCESS | `orders=29`，`buys=29`，`held_skipped=21` |

包自检：`verify_package_sha(2026-08-24) => {'ok': True, 'errors': []}`。

## 验证

- 根因相关回归：72 项通过。
- Shadow 封包链：27 项通过。
- Shadow 执行链：40 项通过。
- 修复后定向回归：67 项通过。
- 完整测试：`2052 passed, 15 skipped, 1 failed`；唯一失败是需要 shell 注入数据库凭据的 `test_pr26a4_upgrade.py::TestL10QuarterlySmoke::test_quarterly_smoke`，不是本次链路代码失败。

当前生产 release：`chenyiyun-prod-20260825-alpha-signal-fix3`，commit `80ec66b1`。
