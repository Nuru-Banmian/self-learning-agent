# Issue #9：学习迁移证据与组合验收

日期：2026-09-20。固定审查基线 `50803a3c67e0b0b2c52f8b49157d9a38360a986f`；公开 HTTP/SSE、浏览器和真实 OS 进程重启为验收边界，数据均隔离。本轮只在当前 main 本地提交，不推送、创建 PR、合并或关闭 Issue。

**实现及本机验证不等于首版完整交付。真实 IQS、和风城市/天气因缺独立凭据与 Host 跳过；大陆无代理出口未验证。AC-09、AC-10 的真实信息服务部分和 AC-12 仍有必要缺口。**

## 实现与证据契约

- 每次执行保存固定模型快照、思考模式和实际预算；每次模型调用记录请求模式、HTTP 状态、可用 token 用量、耗时，错误不回显供应商正文或密钥。用量缺失为 null，不等于零。
- 保存记忆时同时保留内容和来源快照；后续编辑/删除不改写历史。加载快照、模型采用说明、人工检查点分别展示；模型采用说明始终不自动令 `effect_verified` 变成 true。
- 页面支持按历史请求 ID 查找、跨会话并排比较及从检查点恢复基线。`POST /api/runs/{id}/checkpoints` 保存具体标准、观察、人工判定和基线；同 ID 重放不重复，冲突拒绝。检查点不是模型工具，不能证明未核验的其他标准。旧记录缺少模型配置或保存快照时明确提示。
- 同句的普通偏好和独立日期待办都可处理；待办校验先检查完整原文，不能拆掉否定授权。生效资料偏好进入实际搜索输入，受已有范围/例外/删除筛选和查询长度限制。

## AC-01～AC-12 映射

本地列使用正常 HTTP/SSE 与真实隔离 SQLite，供应商响应受控；“真实百炼”列只表示模型是真实调用。浏览器供应商类型另列，不将模拟信息当作真实检索质量。历史记录明确标注，不冒充本轮重新执行。

| 标准 | 本地确定性测试 / 模拟供应商入口 | 真实模型组合证据 | 浏览器证据 | 真实信息服务 / 大陆无代理 |
| --- | --- | --- | --- | --- |
| AC-01 待办完整流程 | `test_chat.py` 创建多项；`test_maintenance.py` 修改、完成、跨会话读回 | `b68170c3c2` learn、plan、accept、done | 本轮普通聊天新增、建议加入、完成和刷新读回 | 不涉及信息服务；大陆未验证 |
| AC-02 日期与歧义 | `test_maintenance.py::test_groups_roll_over_at_user_midnight_without_moving_tasks`；聊天日期/目标歧义测试 | learn 使用固定当地日期；天气 outside 明确 partial/empty | 本轮显示 2026-09-20 待办、2026-09-21 天气 | 实际大陆日期场景未验证 |
| AC-03 跨会话与重启 | `test_process.py` 待办、记忆、检查点实际进程重启；`test_process_recovery.py` 强制结束 | 三次进程启动、两次重启，来源与检查点读回 | 本轮 PID 55072 停止、53276 启动；新会话继续查询计划 | 大陆未验证 |
| AC-04 建议与写入 | `test_research.py::test_no_search_for_todo_query_and_accept_exercise_once`；天气建议授权；混合句全原文授权回归 | plan 前后待办相同，明确 accept/done 后读回 | 本轮待办 3→生成建议仍3→明确加入4→完成后仍4；已加入按钮禁用 | 真实信息服务不计通过 |
| AC-05 普通对话学习 | `test_memory.py` 普通偏好、引用/推测拒绝；`test_evidence.py` 偏好+待办同句 | learn 保存偏好和待办，来源原文一致 | 本轮普通聊天保存1条偏好；另有候选拒绝提示如实保留 | 真实百炼通过；大陆未验证 |
| AC-06 维护与范围 | `test_memory_maintenance.py` 纠正/编辑/删除/旧来源；`test_memory.py` 临时条件跨零点、当前例外和任务范围 | correct/corrected/deleted：搜索转为视频，删除后 loaded=0 | 历史对比可恢复，旧内容保留为当时快照；维护交互历史复用 #5 | 临时过期为受控时钟；大陆未验证 |
| AC-07 迁移与证据 | `test_evidence.py` 保存、加载、来源、采用、人工核验、无自动效果通过；`test_process.py` 无旧聊天 | baseline→learn→新会话plan→transfer新主题→corrected→deleted；request-shapes.jsonl 无旧完整对话 | 本轮历史 transfer/baseline 并排、保存检查点、刷新、基线恢复与视觉检查 | 真实搜索质量未验证 |
| AC-08 三角色与状态 | `test_evidence.py` model_result 模式/用量/脱敏；各角色 HTTP/SSE 测试 | learn 真实学习+主角色，research 主+执行；partial/empty/error 与结果一致 | 本轮聊天、记忆、搜索/天气依据和请求状态可见 | 真实百炼通过，IQS/和风跳过，大陆未验证 |
| AC-09 资料与来源 | `test_research.py` 实际工具响应来源、摘要/正文、部分失败、预算和只读范围 | 百炼+模拟IQS：plan/transfer/corrected/deleted，查询及引用对应 | 本轮显示查询、来源、正文标记与可选练习 | **真实 IQS skipped；大陆 unverified** |
| AC-10 外出与天气 | `test_weather.py` 明确地点/日期、重名、时区、超范围、失败、无结果 | 百炼+模拟和风：outing/resolved success；ambiguous/outside empty；weather-failure error | 本轮上海外出主线；历史 #7 重名/超范围/失败浏览器证据复用 | **真实和风 skipped；大陆 unverified** |
| AC-11 失败与重复请求 | `test_reliability.py`、`test_run_recovery.py`、`test_process_recovery.py`，检查点重放与冲突 | 同一建议接受防重复、失败不伪装天气成功 | 本轮重跑断网、进程中断与显式重试、双标签排队；见下文 | 真实供应商故障注入未做；大陆未验证 |
| AC-12 大陆真实联调 | 本地不作为该项替代 | 百炼固定快照工具调用/JSON Schema、用量/耗时已记录；仅本机结果 | 浏览器连本机不证明出口位置 | **整项未通过：网络位置未验证，IQS/和风配置缺失** |

## 真实组合记录与保留的失败

可重复入口及预算见 [issue-9-demo.md](issue-9-demo.md)。原始输出位于被忽略的 `output/issue9/live/`；每次目录独立，私密配置不入库。下列是实际运行记录，不把早期聚焦测试或 Issue #8 的 166 passed 当作本轮最终全量。

| 目录 | 结果与处置 |
| --- | --- |
| `5abdf1b88c` | failed：验证脚本访问不存在的 research.steps；改核对公开回答的步骤和来源 |
| `1703a0cfc0` | failed：真实模型普通偏好+今日待办未保存且转入研究；公开回归后修复混合分句 |
| `f98482c5c2` | failed：新记忆虽加载，实际搜索没携带视频偏好；公开回归后使实际工具查询携带生效偏好 |
| `5638a24a12` | failed：验证客户端10秒读超时；客户端改135秒，应用总预算仍120秒 |
| `b68170c3c2` | passed，范围严格为真实百炼+模拟IQS/和风；全部步骤见 results.json，重启与授权检查通过 |
| `d9dcabce65` | skipped：缺少 IQS/和风配置；独立 live_research / live_weather 同样 SKIP |

成功组合固定 `qwen3.7-plus-2026-05-26`、关闭思考。模型最多3次、总轮次120秒、单次30秒/重试1；搜索/天气各最多4次、单次12秒/重试1。资料轮观测约5.5–7.7秒，天气约1.8–2.8秒，仅为该次观测。`execution` 与 `events.model_result` 保留模式/用量/耗时；`research.calls`、`weather.calls` 保留信息服务模拟实际结果。`trust_env=False` 不能证明大陆出口。

## 本轮续接：公开回归与浏览器

- 新发现混合句 `我喜欢优先阅读官方资料但不要保存待办，今天我要学习 Python 生成器` 绕过原文否定：新增公开测试初次 **1 failed、1 passed**（问号场景已正确拒绝）。修复先检查完整原文的授权，才拆独立事项；`test_evidence.py + test_chat.py` **27 passed**、mypy通过。该修复收紧拒绝范围，不更改成功组合的合法输入路径。
- 浏览器用 `b68170c3c2/acceptance.db` 的副本 `output/issue9/browser-continuation.db`，不改原始成功数据。另起真实百炼+模拟信息服务，两条主线实测见 `output/playwright/issue9-learn.yml`、`issue9-restarted.yml`、`issue9-plan.yml`、`issue9-accepted.yml`、`issue9-weather.yml`。
- 学习聊天保存1条偏好、1条待办，存在额外候选被拒绝提示，不能表述为所有候选都保存。实际进程重启后新会话生成计划，展示搜索输入/来源/步骤/可选练习。明确点击加入后多1项，完成后移入已完成；天气主线未擅自加入准备事项。
- 原成功运行的 transfer 检查点能恢复 baseline，左右加载分别1/0，模型相同、会话不同。浏览器另存具体人工检查点，刷新后仍可查看，默认新判定为未验证。截图初查发现面板无内边距，已补齐表单和卡片边距；属于低影响布局修正。
- 交接前 `f98482c5c2` 数据的浏览器保存/刷新记录属于早期历史证据，本轮以成功目录副本重新验收，不能混称。
- 新证据布局已查看桌面完整图 `output/playwright/issue9-comparison-final.png`、局部图 `issue9-evidence-desktop.png` 和窄屏图 `issue9-evidence-mobile.png`；390px视口下文档宽375px，无横向溢出，长ID和来源可换行。人工检查点保存后刷新及按钮恢复见 `issue9-checkpoint-restored.yml`。
- 故障浏览器复验使用全模拟供应商 `tests.recovery_demo` 和独立 `output/issue9/recovery-continuation.db`。请求处理中 offline，放行模型，online/刷新后待办从1到2、状态completed；`issue9-offline.yml` 和 `issue9-offline-restored.yml` 保留过程。初次 run-code 写法错误未触发断网，改为函数形式重新执行后才计通过。
- 浏览器等待模型时停止 PID 47068，重启 PID 43684，页面自动显示“处理已中断，未保存待办”，数量仍2；点击显式重试后数量3，原中断记录保留。证据 `issue9-interrupted.yml`、`issue9-retry-completed.yml`。断网和停止进程期间有预期网络错误，不声称浏览器零错误。
- 同会话双标签先后提交两个不同请求：`issue9-queued.yml` 同时显示“正在处理”和“排队中 · 前面还有1轮”；放行后 `issue9-queue-completed.yml` 两轮完成，待办3→5。两次都是明确新请求，区别于同ID重放（由公开HTTP并发测试核验）。这些快照均在 `output/playwright/`。
- Spec修复后的真实百炼+模拟IQS浏览器补验：`issue9-source-exception.yml` 中“只找视频教程，不要官方文档”加载0条长期偏好、实际查询为“Python 生成器 视频教程”；再开新会话，`issue9-preference-resumed.yml` 加载原1条偏好、实际查询再次包含官方资料。当前记忆、待办、会话、run和检查点公开读回另存 `issue9-browser-readback.json`。浏览器工具曾停在about:blank导致截图定位超时；重新导航后恢复原会话，无须重发业务请求，最终截图已重新生成并查看。

## Standards

独立只读审查 `50803a3…81d0f38`：文档标准违反0项、可操作代码异味0项。增量 `81d0f38..8a0a6b5` 复审仍为0项，未发现违反领域术语、公开测试边界、凭据保密或角色授权的变化。

## Spec

初审1项P2：无“这次/本次”的当前明确资料例外仍被追加长期偏好，违反 SPEC.md 的“当前任务的具体要求优先于一般偏好”。公开回归最初因夹具局部变量遮蔽未产生搜索，修正夹具后准确得到失败：用户要求只找视频、不要官方文档，实际查询却追加官方偏好。修复记忆选择，使当前明确资料限制优先，持久偏好不改写；回归同时核验后续普通新会话继续使用长期偏好。

修复提交 `8a0a6b5`，相关4个文件聚焦测试 **96 passed**；独立Spec复审两条关键公开回归 **2 passed**，剩余可操作代码问题0项。新增范围扩张0项。真实IQS/和风及大陆验收缺口持续保留。

## 最终门禁

基于实现 `8a0a6b5`，审查修复后完整 `python -m pytest -q` **175 passed，2 warnings，64.56秒**。两条为既有 Starlette/httpx 与 anyio 弃用警告。mypy（14个app源文件）、Ruff check、Ruff format --check（36文件）、前端 TypeScript/Vite build、`git diff --check` 全部通过。最终全量运行一次；之后只补证据文档，不改变程序行为。

| 验证层 | 最终状态 |
| --- | --- |
| 本地确定性 / 外部供应商模拟 | passed：175项公开接口与真实进程测试 |
| 真实百炼+模拟IQS/和风组合 | passed：历史成功组合及本轮两主线浏览器；修复后当前例外与恢复长期偏好真实模型补验通过 |
| 浏览器新增证据、布局与恢复 | passed：保存/刷新/基线恢复、两主线、断网、进程中断重试、双标签队列；故障期间网络报错已保留 |
| 真实IQS | skipped：缺独立凭据 |
| 真实和风城市/天气 | skipped：缺独立凭据与Host |
| 中国大陆无代理网络 | unverified：无法确认出口位置；AC-12未通过 |
| 首版完整交付 | **未达成**：真实信息服务与大陆网络仍为必要缺口 |

Standards 0项；Spec 初审1项P2已修复，复审0项剩余代码问题。Issue #9保持OPEN，后续真实服务验证须由具备凭据和可确认大陆无代理环境继续执行。

## 后续发布授权

用户随后明确要求“上传pr合并然后关闭”，授权推送、创建并合并PR、关闭Issue #9。上文仅本地提交及OPEN状态记录实现阶段；发布按本次新授权进行。关闭Issue不改变AC-09、AC-10真实信息服务与AC-12大陆无代理验收的未完成状态，不代表首版完整交付。发布阶段仅补充授权说明，应用代码仍为已通过最终门禁的版本。
