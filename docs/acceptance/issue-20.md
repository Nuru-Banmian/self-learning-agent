# Issue #20：利用记忆并持久续接学习需求

实现基线：`8b4d2d484f1ce7edbf48746dbb67e0bf6903333b`。本轮仅在当前 `main` 本地提交，不推送、不创建 PR、不关闭 Issue。

## 行为与边界

- 简短学习意图先核对学习目标及影响本次路线的基础、可用时间；已有信息来自用户原话或当前生效记忆，仅追问缺失项。单纯“学习 Redis”不视为具体目标。
- `GET /api/learning-requests` 返回跨会话保存的需求、原话、上次已知约束、待答问题及关联路线。界面将历史快照与续答时重新读取的记忆区分开。
- 自然语言补充复用聊天 HTTP/SSE 路由。面板明确选择目标时发送 `continue_learning` action，参数为 `request_id`，消息内容是实际补充。
- SQLite 保存需求版本、处理请求与记忆版本。每次续答重新读取生效记忆，不将历史记忆快照重新注入模型。路线和需求完成关联在同一事务提交，提交时再次核对记忆版本。
- 多个可能目标时澄清，不任意关联；重复回复、并发请求、SSE 重连与显式重试不重复生成同一待续需求的路线。生成或澄清不创建待办。
- 复用已有记忆提取与范围校验，仅扩展“我有…基础”的明确背景表达；一次性条件保持原有范围规则。学习上下文最多加载 6 条、4000 字符的生效记忆；单项需求最多保留 20 条、16000 字符的用户补充，不加载完整历史聊天。

## 逐项验收

| 要求 | 验证 |
| --- | --- |
| 背景减少追问；信息充分直接搜索 | `test_learning_requests.py` 背景记忆用例；既有 `test_roadmaps.py` 完整需求用例；真实模型只追问目标 |
| HTTP 保存目标并续接到路线，不创建待办 | `test_missing_goal_survives_restart_and_new_session_reply`；公开查询与 SSE 事件读回 |
| SQLite、刷新、进程重启、新会话和目标歧义 | `test_learning_request_recovery.py` 真正终止并重启 Uvicorn；两目标选择测试；浏览器演示 |
| 当前纠正、记忆删除/失效、一次性条件 | 本轮从零开始覆盖旧背景但不改长期记忆；删除背景后重新追问；跨日时间记忆失效后重新追问 |
| 并发、重放、重试、生成期间记忆变化 | 双会话竞争、同/不同请求 ID 重放、Last-Event-ID；生成期间删除记忆后拒绝提交并在重试时重新核对；进程中断后仅生成一条路线 |
| 自然语言路由、真实模型与来源 | 以下真实百炼/IQS 和模拟浏览器证据，分别说明 |

## 真实服务验收

运行 `.venv/Scripts/python -m tests.live_learning_requests`，使用独立数据库，不读取或修改用户日常数据。

- 首次：`output/issue20/live/c845ffb7fc/results.json` **失败**。模型将“学习 Redis”提取成目标，提前生成了路线。保留失败记录；增加公开接口回归测试和主题重复校验后修复。
- 复验：`output/issue20/live/7ba48caed4/results.json` **通过**。真实百炼、真实 IQS；先保存 Python 基础与官方资料偏好，仅追问具体目标。实际停止并重启进程后，在新会话回答“做一个带过期时间的缓存，主要给自己的 Python 小项目用”，生成一条有来源路线；重复原回复仍仅一条路线，待办为零。
- 路线结果为 `partial`：IQS 返回主要是第三方资料摘要，应用说明官方来源缺口。不能宣称官方资料偏好充分满足、代码练习逐项执行通过或模型语义质量全面验证。本轮没有独立核验中国大陆出口。

## 浏览器演示

本轮使用真实浏览器、运行中的 FastAPI 与 SQLite，**百炼和 IQS 响应均模拟**，工厂为 `tests.learning_request_demo:create_demo_app`。

已操作：聊天保存 Python 基础 → 发送缺少目标的 Redis 意图 → 只追问目标 → 浏览器刷新 → 新会话仍显示原目标及已知背景 → 在目标面板补充“目标是实现缓存” → 查看带来源路线；待办保持 0，浏览器 console 为 0 errors / 0 warnings。

- 数据库：`output/issue20/browser/demo.db`。
- 新会话仍可续答：`output/playwright/issue20-pending-new-session.png`。
- 有来源路线：`output/playwright/issue20-sourced-route.png`。
- 操作后快照：`.playwright-cli/page-2026-09-21T04-06-09-853Z.yml`。

## 本地检查

- 新增澄清与真实进程恢复专项：13 项；路线与澄清联合专项 46 passed，2 条既有 Starlette/httpx/AnyIO 弃用提示。
- 审查前完整回归：230 passed，2 warnings，85.53 秒，`output/issue20/full-suite.txt`。
- Standards 初审 0 项；Spec 初审 3 项 P2：非必要时间预算被强制追问、临时偏好跨目标污染、已完成需求的旧回复误投另一待续目标。均补公开接口失败回归并修复，联合专项 46 passed。最终完整回归和复审结果随后补记。

原有未提交的 `CONTEXT.md`、`HANDOFF.md` 和 `docs/plans/` 均保留，未纳入本轮提交。
