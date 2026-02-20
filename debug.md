# Debug Log: MySQL Startup Issues

## Case 1: MySQL failed to start after reboot (2026-02-20)

### 症状 (Symptoms)
- 系统重启后，MySQL 服务未自动启动。
- 执行 `brew services start mysql` 报错：`Bootstrap failed: 5: Input/output error`。
- 手动运行 `mysqld` 可以启动，但通过 `brew services` 无法启动。

### 根因 (Root Cause)
Homebrew 的启动配置文件（launch agent plist）中硬编码了错误的 `datadir` 参数，覆盖了 `/opt/homebrew/etc/my.cnf` 中的配置。
- **错误路径**: `/opt/homebrew/var/mysql`
- **正确路径**: `/Volumes/extension/mysql` (外部硬盘)

### 修复方案 (Permanent Fix)
1.  **修改 plist**: 修改 `~/Library/LaunchAgents/homebrew.mxcl.mysql.plist`，移除 `--datadir` 和 `WorkingDirectory` 参数，确保其读取 `my.cnf`。
2.  **清除系统残留属性 (Critical)**: 如果遇到 `Bootstrap failed: 5: Input/output error`，通常是因为目录或文件被 macOS 隔离。执行以下命令：
    ```bash
    # 清除目录和文件的扩展属性
    xattr -c ~/Library/LaunchAgents/
    xattr -c ~/Library/LaunchAgents/homebrew.mxcl.mysql.plist
    # 重新加载服务
    launchctl bootout gui/$(id -u)/homebrew.mxcl.mysql
    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/homebrew.mxcl.mysql.plist
    ```

### 后续注意事项 (Future Checklist)
1. **Brew 升级**: 运行 `brew upgrade mysql` 后，Homebrew 可能会还原 `.plist` 文件。如果 MySQL 再次启动失败，请检查并重新移除 `--datadir` 参数。
2. **挂载延迟**: 如果重启后 MySQL 未启动，可能是因为外部硬盘挂载慢于服务启动。尝试手动重启服务：
   ```bash
   brew services restart mysql
   ```
3. **手动启动**: 如果 `brew services` 仍然不稳定，可以使用 mysql 工具自带的脚本启动：
   ```bash
   mysql.server start
   ```
4. **硬盘连接**: 确保外部硬盘 `/Volumes/extension` 已正确挂载：
   ```bash
   df -h | grep extension
   ```
