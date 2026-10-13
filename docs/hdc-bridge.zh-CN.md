# 鸿蒙本地执行模式（HDC）

适用于 HarmonyOS 原生手机和 DevEco Studio 本机模拟器。手机或模拟器无需安装 SAB 辅助 App；在连接设备的电脑上运行 HDC 桥接程序，AI 分析仍使用 SAB 服务端当前账号的模型。

1. 安装 DevEco Studio 或鸿蒙 SDK，手机开启开发者模式和 USB 调试并允许电脑连接，或启动本机模拟器。
2. 执行 `hdc list targets`，记录设备标识，例如 `127.0.0.1:5555`。多设备时每台使用独立标识。
3. 在 SAB「设备」页展开「鸿蒙本地模式 · HDC · 手机和模拟器」，填写设备标识和名称，点击「添加桥接设备」。
4. 下载桥接配置 `sab-hdc-bridge.json`，在项目根目录执行：

```sh
python3 -m venv .venv-bridge
.venv-bridge/bin/pip install -r tools/adb-bridge-requirements.txt
.venv-bridge/bin/python tools/hdc_bridge.py --config ~/Downloads/sab-hdc-bridge.json
```

HDC 不在 PATH 时，在命令后加 `--hdc /完整路径/hdc`。macOS 常见路径为 `/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc`。可先执行 `tools/hdc_bridge.py --list` 检查设备。
配置含设备控制凭证，只保存在运行桥接的电脑上。脚本向平台发起连接，不开放本地控制端口。设备断开后自动重试，同一凭证的重复连接会拒绝。

设备在线后，在快速任务或套件选择带「鸿蒙」标记的设备。桥接支持：

- 截图刷新预览、UI 树、元素点击、坐标点击、滑动。
- 返回、主页、回车、删除、Tab 按键。
- 已安装应用列表、启动和停止应用。包名使用鸿蒙 bundleName，activity 字段可填写 Ability 名称；留空时从已安装 bundle 元数据读取入口，不猜测 Android Activity。
- 已聚焦输入框的 ASCII 文本输入；暂不支持中文及自动清空输入。

截图遵循 SAB 的半分辨率坐标协议，AI 图片坐标由服务端转换为设备坐标。滑动时长近似转换为 `uitest` 的速度参数。
设备平台由 HDC 连接类型确定为 `harmony`，AI 决策、规划、视觉识别、校验及知识检索使用鸿蒙平台，不采用 Android 专用导航或 API。

当前预览是周期截图刷新，不是视频流；尚不支持视频录制、实时视频、安装 HAP/APK、最近任务和通知栏操作。AI 不提供安装 APK 工具，其他不支持操作返回明确错误。
使用模拟器验证不等于真机或示例项目业务验收；业务用例仍需在对应环境实际执行。
