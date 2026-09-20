# Issue #9 可重复演示

先按 README 配置 Python/Node、安装锁定依赖并构建前端。后端私密 `.env` 配置百炼、IQS、和风独立凭据及和风 Host。固定模型 `qwen3.7-plus-2026-05-26`，关闭深度思考。

## 自动组合验证

```powershell
.\.venv\Scripts\python -m tests.live_acceptance --output output/issue9/live
# 缺少 IQS / 和风时，可显式验证真实百炼和模拟信息服务：
.\.venv\Scripts\python -m tests.live_acceptance --mock-information --output output/issue9/live
```

每次创建新的隔离目录，保存真实临时 SQLite、各步骤公开 run 快照、调用请求角色形状与 results.json；不覆盖初次失败。只在设置真实凭据后发起供应商调用，会产生 API 费用。脚本真实启动、停止并重启 Uvicorn，通过相同 HTTP/SSE 执行；不使用个人数据库。默认缺全部信息服务凭据时标记 skipped；模拟模式明确标记 real-model/mock-information，不计入真实 IQS/和风通过。

验证：无相关记忆 → 普通聊天形成偏好与待办 → 实际进程重启 → 新会话当天计划 → 建议无写入 → 明确接受、防重复和完成 → 新学习主题 → 纠正后查询变化 → 删除后停止加载 → 天气/消歧 → 历史证据与检查点重启读回。模拟信息模式还含指定城市 ID 重查、超范围和 403；临时条件跨零点、异常中断/断线等确定性复验见下列入口。

调用预算：组合脚本显式 `MAX_MODEL_CALLS=3`、`RUN_TIMEOUT_SECONDS=120`，以容纳学习、主 Agent 和资料汇总；单次模型超时仍取本地配置（本次为 30 秒），模型重试 1。搜索和天气各最多 4 次、单次 12 秒、重试 1。实际配置保存在 run.execution；实际调用和可用用量见 events 的 model_result、research.calls 与 weather.calls。不承诺未测时延或费用。模拟信息模式时钟固定 2026-09-20，方便稳定核对日期；全真实模式使用当前时钟。

## 浏览器复演

使用独立数据库启动，先构建前端：

```powershell
$env:DB_PATH = 'output/issue9/browser-demo.db'
$env:MAX_MODEL_CALLS = '3'
$env:RUN_TIMEOUT_SECONDS = '120'
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8019
```

1. 打开 `http://127.0.0.1:8019`，新会话询问“请查找 Python 生成器学习资料，并给一个小练习”。复制请求记录内 ID 作为无记忆基线。
2. 新会话表达“我喜欢优先阅读官方资料，今天我要学习 Python 生成器”。核对记忆原文、来源与真实待办。
3. 停止实际应用进程，再用同一命令启动；新会话查询“今天我该干什么？请查找学习资料并给一个可选练习”。检查返回来源、步骤和待办数量。明确点击加入一条建议，再标记完成并刷新核对。
4. 新会话询问 Python 装饰器资料。在“学习迁移证据”选择此回答，输入步骤 1 的基线 ID，对比固定模型、会话、加载与实际回答。针对实际搜索输入/来源、步骤或待办变化填写检查点；未知效果选未验证。
5. “更正：我喜欢优先观看视频资料”，新会话重复新主题查询，检查实际查询及结果变化；删除偏好后再查询，确认不再加载。历史证据仍保留先前状态。
6. 查询“明天上海天气，出门需要准备什么？”，核对地点/日期/来源，重名城市应消歧；超出预报窗口和服务失败不得冒充成功。
7. 刷新页面，输入之前请求 ID 恢复证据，并点击检查点中的基线按钮恢复对比。仅模型声称采用仍显示未验证。

只有模拟服务可用时，可设置 `$env:ACCEPTANCE_MOCK_INFORMATION='1'` 并以 `tests.acceptance_app:create_acceptance_app` 为 factory；百炼仍为真实调用，所有 IQS/和风资料都为模拟，不能据此核查真实网站内容。

## 确定性验收入口

```powershell
.\.venv\Scripts\python -m pytest tests/test_evidence.py tests/test_memory.py tests/test_memory_maintenance.py -q
.\.venv\Scripts\python -m pytest tests/test_research.py tests/test_weather.py -q
.\.venv\Scripts\python -m pytest tests/test_process.py tests/test_process_recovery.py tests/test_run_recovery.py -q
```

全部供应商模拟但使用正常公开业务接口和真实隔离 SQLite。进程测试实际结束 OS 进程；普通对象重开不冒充进程重启。大陆出口须由可确认的大陆无代理环境另行执行全真实命令，记录网络条件；`trust_env=False` 本身不能证明大陆网络。
