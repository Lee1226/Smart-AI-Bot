# Scrcpy 实时画面链路（实验分支）

本分支将 **非 Portal 的本地 Android 实时画面** 改为服务端 Scrcpy 采集，再经 WebSocket 发送 H.264 数据给现有前端解码/渲染组件。Portal 设备仍保留原有远程 H.264 通道；鸿蒙 HDC 与 iOS 的截图路径不变。

## 数据流

`Android 设备 --ADB--> scrcpy-server --TCP forward--> Smart-AI-Bot 后端 --WebSocket/H.264--> H264Canvas / H264Player`

- 采集：Scrcpy server，默认 max size 1280、码率 4 Mbps、H.264。
- 传输：后端解析 Scrcpy 元数据/帧包，向浏览器转发 H.264 配置包（SPS/PPS）与视频负载。
- 播放：复用现有 H.264 解码组件；暂时保留原来的 WebCodecs / jmuxer 兼容回退。
- 认证：WebSocket 继续校验 Smart-AI-Bot 登录 token，并限制为当前账户设备及 owner/admin。
- 多设备：每台设备使用独立的 ADB serial 和按设备 ID 派生的转发端口。

## 前置条件

1. 后端运行机器安装 Android Platform Tools，且 `adb` 在 PATH 中。
2. 手机打开 USB 调试，执行 `adb devices` 后状态必须为 `device`，不能是 `offline` 或 `unauthorized`。
3. 下载**与后端代码内版本号一致**的官方 `scrcpy-server` 文件。当前服务按文件名默认识别 v3.3.4；服务端和手机端启动参数中的 Scrcpy 版本必须匹配，否则协议握手会失败。请只从 [Genymobile/scrcpy 官方仓库](https://github.com/Genymobile/scrcpy) 获取发行文件。
4. 将文件放到 `backend/scrcpy-server-v3.3.4`，或设置 `SCRCPY_SERVER_PATH` 指向该文件的绝对路径。

## 设备映射

后端只有一台 ADB 设备在线时会自动选择该设备。若同时连接多台手机，必须在后端进程环境变量 `SCRCPY_DEVICE_MAP` 中配置 Smart-AI-Bot 的设备 ID 到 ADB serial 的映射，避免把画面串到另一台设备。

Windows PowerShell 示例（请替换成实际设备 ID 与 serial）：

```powershell
$env:SCRCPY_DEVICE_MAP = '{"<Smart-AI-Bot设备ID>":"<adb devices显示的serial>","<另一个设备ID>":"<另一个serial>"}'
$env:SCRCPY_SERVER_PATH = 'E:\\tools\\scrcpy-server-v3.3.4'
cd E:\\gitdev\\Smart-AI-Bot\\backend
python main.py
```

如果项目通过 uvicorn 启动，请在同一个 PowerShell 会话设置上述环境变量后，使用项目原有启动命令启动，不要另起一份服务。

## 验证步骤

1. 执行 `adb devices`，确认目标手机状态为 `device`。
2. 在设备页面打开实时画面，选择“视频流”。
3. 后端日志应出现 Scrcpy server 推送、启动和 TCP 连接成功记录。
4. 如果出现“未找到 scrcpy-server”，检查文件路径；出现版本/协议错误，检查 server 文件版本；出现“Configure SCRCPY_DEVICE_MAP”，按上文配置多设备映射。
5. 如果浏览器回落到截图，先检查浏览器控制台与后端日志中的 WebSocket 关闭原因。

## 当前限制与安全说明

- 这是第一版接入，不代表已在用户的 Windows/OPPO/Harmony 设备上完成实机验证。
- Scrcpy 采集要求后端所在机器能通过 ADB 直接访问目标 Android 手机。仅通过浏览器 WebUSB 或远端 Portal 连接的设备，不能自动被本机 ADB 发现。
- 为避免破坏设备上其他应用，本实现不会执行 `pkill -9 app_process` 这类宽泛杀进程操作。
- 端口映射使用设备 ID 派生的端口；极少数情况下若发生哈希冲突，需要调整端口分配策略。
- 本分支只改变实时视频链路，不移除设备注册、操作控制、Portal/WebUSB 连接相关功能。
