# Issue #20：利用记忆并持久续接学习需求

实现基线：`8b4d2d484f1ce7edbf48746dbb67e0bf6903333b`。本轮仅在当前 `main` 本地提交，不推送、不创建 PR、不关闭 Issue。

## 行为与边界

- 简短学习意图先核对学习目标及影响本次路线的基础、可用时间；已有信息来自用户原话或当前生效记忆，仅追问缺失项。单纯“学习 Redis”不视为具体目标。
- `GET /api/learning-requests` 返回跨会话保存的需求、原话、上次已知约束、待答问题及关联路线。界面将历史快照与续答时重新读取的记忆区分开。
- 自然语言补充复用聊天 HTTP/SSE 路由。面板明确选择目标时发送 `continue_learning` action，参数为 `request_id`，消息内容是实际补充。
- SQLite 保存需求版本、处理请求与记忆版本。每次续答重新读取生效记忆，不将历史记忆快照重新注入模型。路线和需求完成关联在同一事务提交，提交时再次核对记忆版本。
- 多个可能目标时澄清，不任意关联；重复回复、并发请求、SSE 重连与显式重试不重复生成同一待续需求的路线。生成或澄清不创建待办。
- 复用已有记忆提取与范围校验，仅扩展“我有…基础”的明确背景表达；一次性条件保持原有范围规则。学习上下文最多加载 6 条、4000 字符的生效记忆；单项需求最多保留 20 条、16000 字符的用户补充，不加载完整历史聊天。
- 续接目标确定后重新选择该目标范围的记忆。先校验搜索引用来自模型实际收到的集合，再移除被排除的引用；重建搜索词时完整保留该目标原话，不截取最后若干字符。若完整搜索条件超过 1024 字符，保存补充并明确提示概括后重新发起，不搜索、不生成路线；不增加模型调用预算。

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
- `7040513` 阶段复验：`output/issue20/live/ce96320cba/results.json` **通过**，日志 `output/issue20/live-final-log.txt`。搜索成功，一次正文读取成功、一次失败，研究结果为 `partial`；此证据早于最后记忆范围修复。
- 最终代码复验：`output/issue20/live/f09798d19b/results.json` **流程通过**，日志 `output/issue20/live-resume-final-log.txt`。真实百炼与 IQS、独立数据库、实际进程停止重启；只追问目标，新会话续答生成 1 条包含 4 个节点的路线，重复回复没有新增路线，待办为零。搜索成功、正文读取 1 成功 / 1 失败，运行与研究结果均如实标记 `partial`。
- 路线结果为 `partial`：IQS 返回主要是第三方资料摘要，应用说明官方来源缺口。不能宣称官方资料偏好充分满足、代码练习逐项执行通过或模型语义质量全面验证。本轮没有独立核验中国大陆出口。
- 最新真实复验覆盖常规自然语言续接链路；记忆范围排除、长续答与超长保护分支由上述确定性测试证明，本次未专门对这些分支再次做真实模型验收。

## 浏览器演示

本轮使用真实浏览器、运行中的 FastAPI 与 SQLite，**百炼和 IQS 响应均模拟**，工厂为 `tests.learning_request_demo:create_demo_app`。

本次收尾未重做浏览器操作，保留交接前演示证据；最后修复仅涉及后端搜索参数和测试，前端最终构建另行通过。

已操作：聊天保存 Python 基础 → 发送缺少目标的 Redis 意图 → 只追问目标 → 浏览器刷新 → 新会话仍显示原目标及已知背景 → 在目标面板补充“目标是实现缓存” → 查看带来源路线；待办保持 0，浏览器 console 为 0 errors / 0 warnings。

- 数据库：`output/issue20/browser/demo.db`。
- 新会话仍可续答：`output/playwright/issue20-pending-new-session.png`。
- 有来源路线：`output/playwright/issue20-sourced-route.png`。
- 操作后快照：`.playwright-cli/page-2026-09-21T04-06-09-853Z.yml`。

## 分阶段检查与独立审查

- 新增澄清与真实进程恢复专项：13 项；路线与澄清联合专项 46 passed，2 条既有 Starlette/httpx/AnyIO 弃用提示。
- 审查前完整回归：230 passed，2 warnings，85.53 秒，`output/issue20/full-suite.txt`。
- Standards 初审 0 项；Spec 初审 3 项 P2：非必要时间预算被强制追问、临时偏好跨目标污染、已完成需求的旧回复误投另一待续目标。均补公开接口失败回归并修复，联合专项 46 passed。`7040513` 阶段完整回归为 **233 passed、2 warnings、87.45 秒**，`output/issue20/full-suite-final.txt`。
- Spec 第二轮发现搜索参数没有随目标记忆重选同步的 P2；交接时已有未提交修复及 45 项专项通过，但尚无该补丁全量结果。本次先补验含进程恢复的四文件联合专项，**47 passed、2 warnings、22.77 秒**，`output/issue20/targeted-resume.txt`。
- 本次独立 Spec 复审又发现一个 P2：重建搜索词仅保留原话最后 480 字，会丢失早先“只看视频、限定 site:bilibili.com”的条件。公开 HTTP/SSE、真实临时 SQLite、模拟模型/IQS 回归先失败后修复：`output/issue20/query-constraint-red.txt` 为 **1 failed、1 passed**，`query-constraint-green.txt` 为 **2 passed**。另一次超长回归先暴露直接返回 Pydantic 错误，证据 `query-limit-red.txt`；随后增加可理解的长度提示且保留补充。
- 最新四文件联合专项：**49 passed、2 warnings、24.25 秒**，`output/issue20/targeted-constraints-final.txt`。覆盖短续答、长续答保留早期来源限制、超限无搜索且需求持久可读回；此层外部服务均模拟，不代表真实资料质量验收。
- 最终代码完整回归：**236 passed、2 warnings、89.57 秒**，`output/issue20/full-suite-resume-final.txt`。两条 warning 为既有 Starlette/httpx/AnyIO 弃用提示；没有失败或跳过。
- 最终 mypy（17 个源文件）、Ruff check、Ruff format check（77 个文件）、前端 `tsc --noEmit && vite build` 与 `git diff --check` 全部通过。日志分别为 `output/issue20/mypy-constraints-final.txt`、`ruff-constraints-final.txt`、`format-constraints-final.txt` 和 `frontend-build-resume.txt`；前端构建完成后只修改了后端、测试和本验收记录。

### Standards

固定点 `8b4d2d4` 至本次最终工作区的独立审查，及约束截断修复后的增量复审均为 **0 项可操作发现**。检查项目文档规则及代码异味；审查员只读，未修改文件。

### Spec

本次初次复审发现上述约束截断 P2；修复后的独立增量复审为 **0 项剩余可操作发现**。审查员另行复跑三个参数场景：**3 passed、12 deselected、2 warnings**，只使用模拟外部服务。

两轴最终剩余发现：Standards 0，Spec 0；此前失败和修复前结果均保留，不以最终通过覆盖历史。

## 本地交付与后续

原有未提交的 `CONTEXT.md`、`HANDOFF.md` 和 `docs/plans/` 共 10 个文件经 SHA256 核对内容未变，未纳入本轮提交。已有本地提交 `2860da4`、`7040513` 保留；本次仅提交运行时修复、对应回归和本验收记录。未推送、未创建 PR、未合并、未关闭 Issue；本次只读核实 #20 OPEN、#19 CLOSED。

后续质量补验应单独验证真实模型的目标范围排除和长约束分支、官方资料偏好满足度、大陆无代理出口及生成练习的实际可执行性。它们不包含在上述通过声明中。任何远程发布仍需用户后续明确授权。
