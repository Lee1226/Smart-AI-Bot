# Mac iPhone / iPad 助手

远程平台无法直接读取你 Mac 的 USB。这个助手在连接设备的 Mac 上运行，主动连接平台，不监听本机 HTTP 端口，也不要求开放公网端口。

## 推荐：本地 Mac 应用

在网页下载对应芯片的 `SAB Device Helper.app` 压缩包，解压后双击应用即可。应用内置 Python、依赖和 USB 转发，无需执行下面的终端命令。

0.2 起同一应用还支持 Android 和鸿蒙：Android 使用 Android SDK 的 ADB，鸿蒙使用 DevEco SDK 的 HDC；应用会自动查找，也可图形化选择工具路径。设备上确认调试授权后，在同一网页助手列表连接，无需另外创建 Token 或运行脚本。WDA 准备仅针对 iOS。

1. USB 连接 iPad，保持解锁，并在设备确认信任及开发者模式。
2. 在应用选择已签名 WDA 的 DerivedData 根目录（首次需要 Xcode 签名并运行 WDA）。
3. 网页点击「配对一台 Mac → 打开本地助手配对」，在应用核对平台地址后点击「配对并连接」。浏览器不允许唤起应用时，点击「复制配对码」粘贴到应用。
4. 网页显示助手在线后点击设备的「连接设备」。应用关闭窗口后仍在菜单栏运行；退出应用会停止它启动的 WDA 和桥接。再次启动会恢复已批准的设备。

应用支持 macOS 14 及以上；安装包按 Apple 芯片（arm64）与 Intel（x86_64）分别构建，未发布的架构不会显示下载按钮。正式外发需 Developer ID 签名和 Apple 公证，本地构建的 ad-hoc 包不代表已公证。

应用凭证保存在 `~/Library/Application Support/SAB Device Helper/host.json`（600 权限）。原命令行助手配置独立保留；同一设备请只运行一种助手。

开发构建、安装包发布见 [macos/README.md](macos/README.md)。

## 命令行方式：首次准备

1. Mac 安装 Xcode，iPhone / iPad 通过 USB 连接并解锁，开启开发者模式并信任 Mac。
2. 在 Xcode 为 WebDriverAgentRunner 配置自己的签名，编译并在该设备运行一次，确认开发者信任和自动化授权。
3. 保留签名构建的 DerivedData 目录；助手使用其中 `Build/Products/WebDriverAgent*.xctestrun` 启动 WDA，不会代替签名。
4. 安装 USB 转发工具：`brew install libusbmuxd`。也可通过 `--pymobiledevice3 /完整路径/pymobiledevice3` 指定已有工具。
5. 在解压的助手目录执行：

```sh
python3 -m venv .venv
.venv/bin/pip install -r adb-bridge-requirements.txt
```

## 配对并连接

在平台设备页的 iOS 区域点击“配对一台 Mac”，复制生成的配对命令，在助手目录执行。命令只对生成它的平台和账号有效，5 分钟过期、只能使用一次，请勿分享。

如 WDA 构建不在默认目录 `~/Library/Developer/Xcode/DerivedData/SAB-WDA`，在命令后加：

```sh
--wda-derived-data /你的/WDA/DerivedData目录
```

助手上线后，回到网页看到这台 Mac 和其 USB 设备，点击“连接设备”。WDA 启动可能需要一段时间，请保持设备解锁并确认设备上的自动化提示。成功后设备会加入屏幕墙。

## 后续使用

保持 Mac 开机、联网，助手终端保持运行。下次在助手目录启动：

```sh
.venv/bin/python ios_host.py
```

首次指定的 WDA 目录和 USB 转发工具会保存，下次无需重复输入；也可再次传参更新。使用自定义 `--config` 时，下次仍需指定该配置路径。已批准连接的设备会在助手重启时恢复；网络短暂中断会自动重连。Ctrl+C 停止助手及它启动的 WDA/桥接进程。

配对凭证保存在 `~/.config/sab/ios-host.json`，权限为 600，不是网页密码，也不是账号 API Key。助手只接受连接当前 USB iOS 设备的请求，不接受任意 Shell 命令。不要分享该配置。网页“解除配对”会使助手及其设备凭证失效。

## 排查

- 助手离线：在配对的 Mac 启动助手，检查能否访问平台 HTTPS 地址。
- 未发现设备：检查数据线、解锁设备、信任 Mac，并在 Xcode 的设备窗口确认 USB 在线。
- 首次准备未完成：检查 DerivedData 目录、签名构建及 USB 转发工具。
- WDA 退出：检查设备的开发者信任和自动化授权，在 Xcode 重新运行已签名的 Runner。
- 同一设备不要同时使用旧的本机直连助手和新的配对助手，以免两个 WDA 启动任务互相影响。
- 配对接口 404：需要先将平台前后端一起更新到支持 Mac 助手的版本。Docker 后端必须包含 `tools` 文件夹，前端代理需允许 `/v1/` WebSocket。

当前支持截图刷新、UI 树、点击滑动、输入和启动应用；暂不支持 iOS 视频流、安装包或通用返回键。
