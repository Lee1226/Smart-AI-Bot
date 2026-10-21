# iOS USB 桥接（WebDriverAgent）

iPhone / iPad 通过 USB 连接 Mac，Mac 上的桥接主动连接 SAB 服务端。网页可以在另一台电脑使用；远程 Mac 的配置中 `server` 必须填写它可访问的 SAB 地址（不能照用网页电脑的 localhost）。但连接手机的 Mac、WDA 和桥接必须保持运行。第一阶段使用截图刷新，不是视频流。

## 准备设备

1. 在 Mac 安装 Xcode，USB 连接手机，在手机信任电脑，开启开发者模式。
2. 使用 Appium 官方 WebDriverAgent（https://github.com/appium/WebDriverAgent），在 Xcode 为 WebDriverAgentRunner 配置开发团队及唯一 Bundle ID；选择目标设备，通过 Product → Test 启动已签名 Runner。按手机提示信任开发者。桥接不管理 Apple 凭据、不自动签名、不安装或重启 Runner。
3. WDA 在设备上监听 8100 端口。在 Xcode「Window → Devices and Simulators」复制 Identifier（UDID），不是 CoreDevice 的另一组 Identifier。
4. SAB「设备」页展开「iOS USB 桥接」，逐台填写 UDID 和设备名称，下载 `sab-ios-bridge.json`。配置含设备控制凭证，请保存在桥接 Mac。

## 启动桥接

在项目根目录执行：

```sh
brew install libusbmuxd
python3 -m venv .venv-bridge
.venv-bridge/bin/pip install -r tools/adb-bridge-requirements.txt
.venv-bridge/bin/python tools/ios_bridge.py --config ~/Downloads/sab-ios-bridge.json --check
.venv-bridge/bin/python tools/ios_bridge.py --config ~/Downloads/sab-ios-bridge.json
```

桥接使用 `iproxy -u UDID -s 127.0.0.1 LOCAL_PORT:8100`，按配置分配不同本地端口（默认 8100、8101 等），限制为本机访问，USB UDID 决定连接哪台手机。已有端口转发占用相同端口时，停止该转发或修改配置的 `port`；不要把 WDA 端口暴露到公网。

若 Homebrew 无法安装，也可在桥接虚拟环境安装 `pymobiledevice3`，启动时加上 `--pymobiledevice3 .venv-bridge/bin/pymobiledevice3`（同样适用于 `--check`）。该转发同样按 UDID 选择设备并只监听 127.0.0.1。

`--check` 只检查 USB 转发及 WDA HTTP 是否可达，不代表页面交互或业务任务通过。设备在线后先在网页确认截图，再执行真实用例。断开后桥接重试；同一设备凭证重复连接会拒绝。

## 支持范围

- 截图刷新、UI 树、元素和坐标点击、滑动。
- 已聚焦输入框的 Unicode 输入及清空；安全输入框的 UI 树不返回内容。
- Home、Enter、Delete、Space。
- 按项目配置的 iOS Bundle ID 启动和停止已安装应用；无需 Activity。
- 复用设备队列、多设备任务和回归。绑定安装包/清理数据的回归准备仍只支持 Android ADB，iOS 会明确拒绝。

iOS 没有通用 Back：使用页面可见的返回按钮或应用手势。此桥接暂不支持应用列表、安装 IPA、数据重置、视频流、录像、最近任务、通知栏及设备日志归档；用例结果和截图证据仍使用平台现有机制。手机锁屏、Runner 停止或 USB 断开都会影响执行。

截图按 iOS 逻辑坐标输出，后端统一缩放一次，并按模型实际看到的图片尺寸映射点击；不会把 Retina 像素直接当作点击坐标。横竖屏时每次重新读取窗口大小。

## 本机一键连接

网页和 SAB 服务运行在连接 iPad 的同一台 Mac 时，可启用内置本机助手。首次签名编译 WDA 后，在启动 SAB 的环境中设置：

```sh
export SAB_IOS_LOCAL_ENABLED=1
export SAB_IOS_WDA_DERIVED_DATA=/你的/WDA/DerivedData
# 使用 pymobiledevice3 时指定完整可执行路径；已安装 iproxy 时无需此项
export SAB_IOS_PYMOBILEDEVICE3=/你的/虚拟环境/bin/pymobiledevice3
```

桥接依赖需要安装在运行后端的 Python 环境中。后端默认连接 `http://127.0.0.1:8000`；使用其他本机端口时设置 `SAB_IOS_LOCAL_SERVER`。只在受信任的本机环境启用该功能，服务应监听回环地址。使用 `http://localhost:8000` 打开设备页，展开 iOS 区域，自动发现已配对的 USB 设备，点击「连接」。助手复用已签名的 `.xctestrun` 启动 WDA，自动注册设备、分配端口并启动桥接；无需手填 UDID 或下载配置。同一账号重复连接复用已有设备。助手配置保存在服务数据目录的 `ios-local` 中。

首次的开发者模式、开发者信任和签名仍需完成；证书过期后需重新签名编译。该功能仅用于本机 Mac 部署，远程服务器继续使用折叠的手动桥接入口。后端停止后需重新点击连接，不会安装系统常驻服务。
