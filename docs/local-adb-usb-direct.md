# 本机 ADB USB 直连

本功能将 Android 手机的 USB ADB 控制从浏览器 WebUSB 移至 Smart-AI-Bot 后端主机。

## 工作方式

- 手机 USB 连接到运行 Smart-AI-Bot 后端的 Windows 电脑。
- Windows 安装 Android SDK Platform-Tools，并确保 `adb.exe` 在后端进程的 `PATH` 中。
- 手机开启「开发者选项 → USB 调试」，连接后在手机上确认 RSA 调试授权。
- 打开「设备管理」，本机 ADB USB 区域自动扫描已授权的 USB Android 设备。
- 点击「连接设备」后，后端保存数据库设备 UUID 与物理 ADB serial 的映射。后续 Agent 操作直接由后端调用 `adb -s SERIAL`。

不需要浏览器 WebUSB 授权、不需要 Portal APK，也不需要手动启动 adb_bridge 脚本。后端不会执行 `adb kill-server`，不会重启共享 ADB Server。

## 目前支持

- ADB USB 设备发现、绑定、断开和重新连接；物理 serial 持久化，设备 UUID 稳定。
- 截图（`exec-out screencap -p`）和 H.264 实时画面（`screenrecord`）。
- UIAutomator XML 层级解析，接入现有 Agent 感知格式。
- 点击、滑动、滚动、Android 按键、返回/主页/最近任务/通知栏。
- 启动应用、强制停止应用、列出第三方应用包名。

## 当前限制

- 原生 `adb shell input text` 对中文和部分 Unicode 输入的兼容性有限；当前遇到非 ASCII 文本会明确报错，不会静默输入错误内容。
- 通过 URL 安装 APK 暂不支持本地直连模式；可用 `adb install` 或继续使用现有 Portal App 连接方式。
- UIAutomator 对部分游戏、Canvas、自绘控件可能无法提供完整控件树；此时仍可使用截图与坐标操作。
- H.264 实时画面依赖 Android 设备的 `screenrecord` 支持。

## 连接类型兼容

新增的 `adb_usb` 是独立的后端本地传输。既有 `portal`、`browser_usb`、`adb_bridge`、`hdc_bridge`、`ios_bridge` 协议与入口保留。旧版 Browser USB 前端实现文件和依赖保留作为回滚参考，但设备页不再把它作为默认 USB 连接入口。

## 数据与安全

- 数据库设备 UUID 与物理 ADB serial 分离保存。
- `adb_auto_connect` 用于记住用户是否主动断开；USB 拔插会离线，重新插入后按此设置恢复绑定。
- 本机 ADB 发现、绑定和断开仅允许 owner/admin。
- 不会自动选择其他 ADB 设备替代已绑定 serial，避免多设备时控制错手机。
- 本地 ADB 设备执行测试时继续使用 Smart-AI-Bot 现有设备任务锁；ADB RPC 另按物理 serial 串行化。

## 回滚

如需临时回滚，可将设备页恢复到原 Browser USB 入口，并保留 `backend/agent/local_adb.py`、数据库新增列和 `adb_usb` 记录。不要删除数据库列或把既有设备记录批量改写为 Browser USB。
