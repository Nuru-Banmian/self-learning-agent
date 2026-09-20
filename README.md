# 自我学习生活多 Agent 个人助理

通过日常聊天记录待办，在新会话中结合用户记忆、资料与天气生成当天计划。

- 主 Agent：统一对话并组织计划。
- 执行 Agent：查询资料、地点和天气。
- 学习 Agent：从日常对话整理用户记忆，在后续任务中使用。

已实现 Issue #2：聊天创建待办、持久化查询、会话与执行记录、最小工作台及 HTTP/SSE。记忆、搜索、天气和待办修改属于后续切片。首版面向本机单人使用。

## 本地启动（Windows PowerShell）

需要 Python 3.13 和 Node.js 22.12+。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
Copy-Item .env.example .env # 仅首次执行；已有 .env 时不要覆盖
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。仅运行一个应用进程，不使用 `--workers`；关闭进程后用相同 `DB_PATH` 重启即可保留数据。默认数据库是 `data/assistant.sqlite3`。前端开发可另开终端运行 `cd frontend; npm run dev`，Vite 将 `/api` 转给 8000 端口。

在后端 `.env` 填写北京地域的 `DASHSCOPE_API_KEY`。`DASHSCOPE_BASE_URL` 默认使用仍受支持的北京兼容地址；也可填该账户的北京业务空间专属地址。不要将密钥放进前端或 `VITE_*` 变量。`.env`、数据库、构建和测试输出都已被 Git 忽略；修改 `.env` 后重启后端。无模型配置时，已保存待办仍能直接查看。

固定模型为 `qwen3.7-plus-2026-05-26`，每次调用显式 `enable_thinking=false`，供应商 HTTP 客户端禁用环境代理。`.env.example` 可配置用户时区、单次模型超时、整轮时限、最多模型调用次数及失败重试次数。最多调用次数包含重试；只重试网络故障、限流及临时服务端故障，不重试鉴权错误或不合法输出。

## 使用与边界

- 输入“请记录明天学习 Python 生成器和整理书桌”，保存后页面读回实际日期和来源。
- 无日期可以说“请记录整理书桌”。支持今天、明天、后天、`YYYY-MM-DD` 和明确月日；省略年份且该月日已过去时先追问。其他日期表达、歧义或无法确认的授权会要求重新明确事项与日期，不猜测写入。
- 授权采用保守的中文指令校验，标题须对应本条消息原文。复杂转述、引用、建议、否定和问题不直接写入；澄清后请重新提交完整事项及日期。
- “新会话”保留待办。查询通过真实持久化数据进行；目前只包含待办创建与查看，不支持修改、完成、自动记忆或执行任务。
- 同一 `request_id` 重试返回原状态；更换内容或会话却复用标识返回 409。同一会话执行中提交另一请求也返回 409。新的请求标识可再次创建相同文本。
- 整批待办、成功事件和执行终态一起提交；失败不会出现部分待办或虚假的成功事件。进程中断后，启动会将未完成请求标记为失败；已成功的请求可以安全重放。

## 公开接口

完整 Schema：运行后的 `/docs`。

| 接口 | 用途 |
| --- | --- |
| `POST /api/sessions` | 创建会话 |
| `GET /api/sessions/{id}` | 读取会话及消息 |
| `POST /api/sessions/{id}/messages` | 提交 `{request_id, content}`，返回 202 和执行状态 |
| `GET /api/runs/{request_id}` | 读取执行状态、实际写入 ID、回复及调用计数 |
| `GET /api/runs/{request_id}/events` | SSE：角色、工具调用/结果、保存、回复与终态，支持 `Last-Event-ID` |
| `GET /api/todos` | 读取稳定 ID、标题、日期、状态、时间戳和来源 |
| `GET /api/health` | 基础状态、时区及是否配置模型，不暴露凭据 |

SSE 的角色和工具事件在实际处理时发出，保存事件仅在事务提交后可见；回复当前以完整片段发出，不宣称供应商逐 token 流式输出。断开 SSE 不取消后台任务，页面可使用同一请求标识重试查询结果。

## 验证

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\python -m ruff check app tests
.\.venv\Scripts\python -m ruff format --check app tests
cd frontend
npm run build
```

显式真实百炼检查（使用本地 `.env`，会产生少量 API 费用，只写临时隔离数据库）：

```powershell
.\.venv\Scripts\python -m tests.live_smoke
```

测试通过公开 HTTP/SSE 使用真实 SQLite，只模拟外部模型或时钟。重启测试实际启动、终止并重新启动 Uvicorn 进程，且重启后移除模型凭据验证数据读取。验收证据与未验证项见 [Issue #2 验收记录](docs/acceptance/issue-2.md)。

## 项目文档

- [正式规格 Issue](https://github.com/Nuru-Banmian/self-learning-agent/issues/1)：已标记 ready-for-agent。
- [首版规格](SPEC.md)：用户故事、技术决策、验收与范围边界。
- [交接记录](HANDOFF.md)：项目由来和用户后续澄清。
- [领域术语](CONTEXT.md)：待办、当天计划、用户记忆和角色定义。
- [协作规则](AGENTS.md)：项目文档和 Issue tracker 约定。

需求、规格与后续开发任务使用 GitHub Issues。仓库不包含 API 凭据。
