# MySQL 启动故障排查记录（2026-02-25）

## 1. 问题现象
- MySQL 无法正常启动，日志反复出现启动后立即退出。
- `mysqladmin ping` 出现过两类报错：
  - `Wrong or unknown protocol`
  - `Access denied for user 'root'@'localhost' (using password: NO)`

## 2. 根因分析

### 根因 A：启动方式错误（用户级 LaunchAgent）
- 存在用户级服务：`~/Library/LaunchAgents/com.chenyiyun.mysql.boot.plist`。
- 该服务每 30 秒执行一次 `scripts/ops/mysql_boot_wrapper.sh`，但以普通用户 `chenyiyun` 运行。
- 而 `mysql_boot_wrapper.sh` 明确要求以 `root` 或 `_mysql` 运行，因此在普通用户下会稳定失败并持续重试。

### 根因 B：数据目录写入权限失败
- 错误日志 `/tmp/mysql_external.err` 关键报错：
  - `mysqld: File './binlog.index' not found (OS errno 1 - Operation not permitted)`
- 该报错导致 MySQL 直接 `Aborting`。
- 本质是运行上下文/权限不正确，MySQL 进程对 `/Volumes/extension/mysql` 写入不通过。

### 根因 C：客户端默认配置导致协议混淆
- `/opt/homebrew/etc/my.cnf` 的 `[client]` 段包含：
  - `socket=/tmp/mysql_external.sock`
  - `host=127.0.0.1`
- `host=127.0.0.1` 会优先走 TCP，和手工指定 socket 的用法冲突，触发 `Wrong or unknown protocol` 等误导性错误。

### 根因 D：认证失败（非服务故障）
- 当服务起来后，`Access denied for user 'root'@'localhost' (using password: NO)` 表示未提供 root 密码。
- 这是认证问题，不是 MySQL 未启动。

## 3. 已执行处理
- 已停用错误的用户级启动项，避免持续失败重试：
  - `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.chenyiyun.mysql.boot.plist`
  - `launchctl disable gui/$(id -u)/com.chenyiyun.mysql.boot`
- 使用正确方式启动（需 sudo/root）后，日志出现：
  - `ready for connections`
- 进程与文件状态确认：
  - `mysqld_safe` 与 `mysqld` 在运行
  - `/tmp/mysql_external.sock`、`/tmp/mysql_external.pid` 存在

## 4. 当前状态
- MySQL 服务：已可启动并运行。
- 认证结论：`root` 账户需要密码，未带密码时报 `Access denied ... (using password: NO)`，属于正常鉴权行为。

## 5. 解决方案（建议落地）

### 方案 1（必须）：固定正确启动路径
- 不再使用用户级 LaunchAgent 直接拉起 MySQL。
- 改为以下任一方式：
  1. 使用 root 级 LaunchDaemon 启动 `mysql_boot_wrapper.sh`
  2. 或用 root 上下文统一管理 MySQL 服务

### 方案 2（建议）：修正客户端配置
- 编辑 `/opt/homebrew/etc/my.cnf`，在 `[client]` 中删除或注释 `host=127.0.0.1`，仅保留 socket。
- 避免 socket 连接被强制转成 TCP。

### 方案 3（建议）：统一认证方式
- 连接时显式带密码：
  - `mysql --no-defaults -h127.0.0.1 -P3306 -uroot -p`
- 如果忘记 root 密码，再执行一次安全重置流程（停库 -> skip-grant-tables -> 改密 -> 正常重启）。

## 6. 快速验证命令
```bash
# 1) 进程检查
ps -ef | rg -i 'mysqld_safe|/opt/homebrew/opt/mysql/bin/mysqld' | rg -v rg

# 2) 日志检查
tail -n 50 /tmp/mysql_external.err

# 3) 端口检查
lsof -i:3306

# 4) 登录验证（带密码）
/opt/homebrew/bin/mysql --no-defaults -h127.0.0.1 -P3306 -uroot -p -e "SELECT VERSION();"
```

## 7. 最终解决方案（已执行）

### 7.1 已执行动作
- 停止并禁用错误的用户级 MySQL 启动器（避免每 30 秒失败重试）：
  - `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.chenyiyun.mysql.boot.plist`
  - `launchctl disable gui/$(id -u)/com.chenyiyun.mysql.boot`
- 使用 `root` 方式执行 `scripts/ops/mysql_boot_wrapper.sh` 启动 MySQL（脚本内部以 `_mysql` 用户运行 `mysqld`）。
- 确认服务就绪：
  - 日志出现 `ready for connections`
  - `mysqld` / `mysqld_safe` 正常驻留
  - `/tmp/mysql_external.sock`、`/tmp/mysql_external.pid` 存在

### 7.2 客户端连接修正（关键）
- `mysqladmin`/`mysql` 出现协议异常时，应优先排除默认配置干扰。
- 推荐连接方式：
  - TCP（推荐，最稳定）：
    - `/opt/homebrew/bin/mysql --no-defaults -h127.0.0.1 -P3306 -uroot -p`
  - Socket（如需）：
    - `/opt/homebrew/bin/mysql --no-defaults -S /tmp/mysql_external.sock -uroot -p`
- 原因：`/opt/homebrew/etc/my.cnf` 的 `[client]` 中有 `host=127.0.0.1` 时，会影响 socket 场景，导致误判为协议问题。

### 7.3 建议的最终配置
- 保留：
  - `[client] socket=/tmp/mysql_external.sock`
- 删除或注释：
  - `[client] host=127.0.0.1`（避免 socket/TCP 混淆）

## 8. 复发时一键处置
```bash
# 1) 先停错误的用户级自动重试（若存在）
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.chenyiyun.mysql.boot.plist 2>/dev/null || true
launchctl disable gui/$(id -u)/com.chenyiyun.mysql.boot 2>/dev/null || true

# 2) 以 root 启动（会要求输入 sudo 密码）
sudo /Users/chenyiyun/PycharmProjects/Chenyiyun2087/scripts/ops/mysql_boot_wrapper.sh

# 3) 验证
tail -n 30 /tmp/mysql_external.err
ps -ef | rg -i 'mysqld_safe|/opt/homebrew/opt/mysql/bin/mysqld' | rg -v rg
lsof -iTCP -sTCP:LISTEN -nP | rg ':3306'
```
