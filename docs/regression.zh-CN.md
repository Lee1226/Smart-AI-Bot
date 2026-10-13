# 多设备回归与 CI 门禁

网页侧栏进入「多设备回归」，选择多个用例套件和在线设备。最多 20 个套件 × 设备组合；每个组合产生独立运行记录。同一注册设备的网页任务和回归任务排队执行，不同设备可并发。录制页手动操作与任务共用设备锁，忙时返回 409；截图预览继续可用。

## 安装包与准备

可上传 APK 并绑定版本标签、SHA-256、应用包名、环境标签、Commit SHA 和构建链接。自动安装和清数据仅支持 Android ADB 桥接；桥接电脑需要 Android SDK build-tools 的 aapt。准备包名必须与套件配置的被测应用一致。安装前校验文件哈希与真实 APK 包名，安装成功后记录真实 versionCode/versionName。桥接电脑必须能访问网页 API 地址。

环境标签仅作追溯，不自动切换服务端地址、网络、地区或数据库。实际准备动作依次是安装、可选清应用数据、启动指定应用。清数据会删除应用登录状态和本地数据，默认关闭。未绑定 APK 时使用设备现有版本。

排队时保存解析后的用例动作与预期快照，执行前检查；套件改变则停止该组合，避免悄悄运行新的动作。失败组合可单独重跑；修改套件后需创建新计划。

## 结果、证据与门禁

只有所有组合正常结束、至少有一个用例且所有用例通过，门禁才允许发布。失败、错误、跳过、取消、服务中断、缺失运行均阻止发布；归档收尾期间仍等待。服务重启把遗留活跃运行标为 interrupted，不自动重放设备动作。

完成后可下载 JUnit XML 和证据 ZIP。ZIP 包含计划/构建/准备元数据、逐用例日志、最终与步骤截图、动作轨迹；ADB 桥接额外采集最近设备 logcat、最近 ANR 和设备信息。这些日志不是按应用过滤的完整运行期日志，可能包含设备其他应用的信息；证据包只允许本账号读取。尚未归档远程视频与网络抓包。诊断采集失败会明确标注，不伪装成成功。

## CI

通过平台账号 API Key 或访问令牌（Bearer）调用，凭据放在 CI secret 中，禁止写进仓库：

```sh
export SAB_URL=http://your-single-worker-server:8000
# 由 CI 注入 SAB_API_KEY
python3 tools/regression_ci.py --config regression-plan.json --output regression-results
# 或观察网页已创建的计划
python3 tools/regression_ci.py --plan-id PLAN_ID --output regression-results
```

配置示例：

```json
{"name":"主干回归","suite_ids":["SUITE_ID"],"device_ids":["DEVICE_ID"],"environment":"测试环境","artifact_id":"OPTIONAL_APK_ID","app_package":"com.example.app","clear_data":false,"max_steps":20,"commit_sha":"BUILD_COMMIT","build_url":"BUILD_URL"}
```

退出码：0=门禁通过，1=业务回归未通过，2=超时/通信/归档错误。超时会阻止发布，但不会自动取消服务器任务；可在网页取消。输出包含 junit.xml、evidence.zip、gate.json。Jenkins 调用后使用 `junit 'regression-results/junit.xml'` 并归档 `regression-results/*`；仅当脚本退出 0 时进入发布阶段。当前没有替你修改或部署线上 Jenkins 流水线。

## 部署边界

目前设备 WebSocket 注册表与队列都是进程内状态，**后端只允许一个 worker、一个实例**；不要多 worker 部署。要扩容需统一设备网关与分布式租约。独立 `backend/cli.py` 是离线本地执行器，不经过服务器队列；CI 应使用上面的服务器客户端，不要与网页共用同一台设备跑旧 CLI。

只有一台物理设备时，跨设备并发只能通过模拟连接验证，不能声称多台真机并发验收。
