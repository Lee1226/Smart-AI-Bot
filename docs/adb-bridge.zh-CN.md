# 本机 ADB 桥接与远程视频

## 多设备桥接

适用于 Android Studio 已经使用手机的电脑。代理通过现有 ADB Server 操作设备，不争抢 WebUSB 接口，也不运行 `adb kill-server`。需要 Python 3.10+、ADB 和已授权 USB 调试的手机。

1. 在连接手机的电脑运行 `adb devices -l`。只有状态为 `device` 的手机可以使用。
2. 在网页「设备」展开「本机 ADB 桥接」，逐台输入序列号并添加设备。
3. 下载桥接配置，将其保存在代理电脑上。配置包含控制凭证，请勿提交到 Git 或分享。
4. 在本项目根目录运行：

```sh
python3 -m venv .venv-bridge
.venv-bridge/bin/pip install -r tools/adb-bridge-requirements.txt
.venv-bridge/bin/python tools/adb_bridge.py --config ~/Downloads/sab-adb-bridge.json
```

Windows 使用 `.venv-bridge\Scripts\python.exe` 和对应 pip。可用 `--adb /完整路径/adb` 指定工具。多个 serial/token 配置在同一个 `devices` 数组，每台独立连接、断线重试和执行。

代理只向平台发起连接，没有开放本机 HTTP 端口。平台地址必须是代理电脑可访问的地址；跨网络推荐 HTTPS。远程部署时平台不需要接入任何 USB 手机。

支持视频、半尺寸截图、UI 树、点击、滑动、按键、应用启动/停止、应用列表。文本输入暂限不含 `%` 的 ASCII，不支持自动清空、APK 安装或交互录制；这些操作继续使用 App 模式。每台设备同时只运行一个任务；多设备可以分别启动相同或不同任务，尚不提供步骤同步。

## App 远程视频

安装本次构建的新版 Smart Agent，按原有流程扫码连接。在手机 App 点击「开启远程视频」，确认 Android 的系统录屏授权。Android 14+ 建议选择共享整个屏幕以观察跨 App 任务。

网页实时画面可选：

- 自动：设备提供视频时使用 H.264，否则截图；视频失败时回落截图。
- 视频流：明确使用视频；未授权或失败时显示提示，由用户选择重试或截图。
- 截图：关闭当前视频订阅，按原有方式获取静态帧。
- 关闭：停止画面预览。

视频仅在观看时通过独立 WebSocket 上传，控制连接不承载视频数据。当前每台设备仅允许一个视频观看者；其他观看者可选截图。结束观看时暂停捕获，保留本次录屏授权；手机点击「停止远程视频」或系统停止共享后彻底释放编码器与录屏授权，需重新授权才能再次观看。旧版 App 继续支持截图。

H.264 预览不包含声音。固定编码尺寸会在旋转或单 App 分享时受系统适配影响，应使用目标手机验证；保密/DRM 页面也可能被系统屏蔽。
