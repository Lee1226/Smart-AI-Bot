# 多设备回归

网页侧栏进入「多设备回归」，选择计划名称、模型、用例套件、在线设备、测试账号和每用例最大步数。最多 20 个套件 × 设备组合；每个组合产生独立运行记录。同一注册设备的网页任务和回归任务排队执行，不同设备可并发。录制页手动操作与任务共用设备锁，忙时返回 409。

创建计划时会保存套件动作与预期快照。任务获得设备独占权后，先检查套件是否发生变化，再根据套件关联的项目应用配置启动被测应用；套件发生变化则停止该组合，避免悄悄运行新的动作。多设备回归不负责上传或安装 APK，也不清除应用数据；请在设备侧准备好被测应用及测试状态。失败组合可单独重跑；修改套件后需创建新计划。

## 结果、证据与门禁

只有所有组合正常结束、至少有一个用例且所有用例通过，门禁才允许发布。失败、错误、跳过、取消、服务中断、缺失运行均阻止发布；归档收尾期间仍等待。服务重启把遗留活跃运行标为 interrupted，不自动重放设备动作。

完成后可下载 JUnit XML 和证据 ZIP。ZIP 包含计划信息、逐用例日志、最终与步骤截图、动作轨迹；ADB 桥接额外采集最近设备 logcat、最近 ANR 和设备信息。这些日志不是按应用过滤的完整运行期日志，可能包含设备其他应用的信息；证据包只允许本账号读取。尚未归档远程视频与网络抓包。诊断采集失败会明确标注，不伪装成成功。

## CI 命令行集成

CI 命令行客户端独立于网页创建表单，可通过平台账号 API Key 或访问令牌（Bearer）创建回归计划、等待质量门禁并下载结果。凭据放在 CI secret 中，禁止写进仓库：

```sh
export SAB_URL=http://your-single-worker-server:8000
# 由 CI 注入 SAB_API_KEY
python3 tools/regression_ci.py --config regression-plan.json --output regression-results
# 或观察网页已创建的计划
python3 tools/regression_ci.py --plan-id PLAN_ID --output regression-results
```

配置示例（model_id 使用模型仓库中的模型 ID）：

```json
{"name":"主干回归","model_id":"MODEL_ID","suite_ids":["SUITE_ID"],"device_ids":["DEVICE_ID"],"test_accounts":{},"max_steps":20}
```

退出码：0=门禁通过，1=业务回归未通过，2=超时/通信/归档错误。超时会阻止发布，但不会自动取消服务器任务；可在网页取消。输出包含 junit.xml、evidence.zip、gate.json。Jenkins 调用后使用 `junit 'regression-results/junit.xml'` 并归档 `regression-results/*`；仅当脚本退出 0 时进入发布阶段。该客户端不会安装 APK、清除应用数据或设置构建元数据。

## 部署边界

目前设备 WebSocket 注册表与队列都是进程内状态，**后端只允许一个 worker、一个实例**；不要多 worker 部署。要扩容需统一设备网关与分布式租约。独立 `backend/cli.py` 是离线本地执行器，不经过服务器队列；CI 应使用上面的服务器客户端，不要与网页共用同一台设备跑旧 CLI。

只有一台物理设备时，跨设备并发只能通过模拟连接验证，不能声称多台真机并发验收。
