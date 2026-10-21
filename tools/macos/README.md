# SAB 设备助手（macOS）

原生 SwiftUI 窗口与菜单栏 + PyInstaller 内置连接引擎。一次平台配对统一连接 Android（ADB）、鸿蒙（HDC）和 iOS（WDA）。运行时不需要用户安装 Python、pip、Homebrew 或 iproxy。iOS USB 通过系统 usbmuxd 转发，仅监听 loopback。

Android 需要 Android SDK 的 ADB；鸿蒙需要 DevEco SDK 的 HDC。助手自动查找 PATH 和常见 SDK 目录，也可用界面中的「选择 ADB / HDC」指定可执行文件。SDK 工具不捆绑分发；连接不依赖 Xcode。iOS 才需要 Xcode、个人 WDA 签名、设备信任与自动化授权。

## 构建

在目标架构的 macOS 上安装 Xcode 后，在仓库根目录执行：

```sh
python3 -m venv .venv-mac-app
.venv-mac-app/bin/pip install -r tools/macos/requirements-build.txt
.venv-mac-app/bin/python tools/macos/build.py
```

输出 `tools/macos/dist/SAB Device Helper.app` 与 `SAB-Device-Helper-macOS-arm64.zip` 或 `SAB-Device-Helper-macOS-x86_64.zip`。每个安装包包含对应架构的 Python 运行时；不能把 arm64 包标为 universal 或 Intel 包。构建目录不进入 Git。

默认使用 ad-hoc 签名，供本机开发验证。公开分发需通过 `SAB_MAC_SIGN_IDENTITY` 指定 Developer ID，完成 Apple 公证与 stapling，再重新压缩并检查安装包。构建成功不代表公证或其他 Mac 的安装验收已完成。

## 发布到平台

平台从 `SAB_MAC_HELPER_DIR` 目录读取上述固定名称的 zip，也支持开发环境默认的 `tools/macos/dist`。将完成签名、公证的包放到该目录后，网页会显示可用架构；没有包时会显示「安装包尚未发布」，不会把源码当成可双击的应用。

Docker 构建可将 zip 放入默认目录一起打包（排除 build 与 .app 目录）；也可以只读挂载发布目录并设置 `SAB_MAC_HELPER_DIR`。下载接口沿用账号鉴权。前后端需一起更新；这不会自动更新已部署的平台。

## 交互与数据

- 网页的 `sab-helper://pair` 只预填平台地址与短期配对码；应用不会自动批准配对。浏览器不支持时可复制配对码，应用端仍需点击确认。
- Swift 与内置引擎使用匿名 stdin/stdout 管道，不开放本机 HTTP API。平台 token 不传给网页、命令行或 URL。
- 应用配置使用独立的 Application Support 目录和 600 文件权限，退出时等待连接引擎释放其 WDA/桥接进程。
- 同一台 iPad 不要同时启动 CLI、本机直连和原生助手，避免 WDA 冲突。
- Android、鸿蒙桥接也由应用管理，支持多台不同平台设备并发连接、重启恢复。ADB 共用系统服务，不会结束 Android Studio 的 ADB。设备映射按平台及序列号隔离；所有桥接子进程只读取自身配置，不共享应用的控制输入管道。
- 平台前后端必须一起更新以识别多平台助手；原来的 iOS 配对和设备映射保持兼容，服务路径仍为 `/api/ios-host` 和 `/v1/providers/ios-host`。

## 验证

```sh
cd backend
SAB_DATA_DIR=/tmp/sab-mac-app-tests .venv/bin/python -m unittest tests.test_ios_hosts tests.test_ios_usbmux tests.test_ios_bridge tests.test_ios_local -q
```

协议测试验证 USB 序列号选择、禁止误连其他设备、loopback 转发及双向数据；接口测试验证账号鉴权与包缺失。最终仍需打开实际应用及网页，用真实 iPad 验证配对、截图和重启恢复。
