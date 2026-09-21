# Issue #21：全部或部分接受路线节点

日期：2026-09-21。规格：[Issue #21](https://github.com/Nuru-Banmian/self-learning-agent/issues/21)，父规格 #18。固定审查基线 `043968d11f7b90f2f37ec522f3b1050360f714ea`。

## 行为与边界

- 路线详情勾选部分节点或全选未加入节点，确认清单列出候选待办名称与数量；按确认按钮才提交。空选择按钮禁用，暂不加入保留路线。
- 新 action `accept_roadmap_nodes` 复用消息入口，参数为 `roadmap_id`、`node_ids`（最多 8 项，可为空）、`expected_version`。稳定节点身份去重，只接受属于指定路线的节点。旧单节点 action 兼容。
- `BEGIN IMMEDIATE` 内一次提交待办、关联、路线版本、请求路线快照、逐节点结果事件、回复及终态。`roadmap_nodes_accepted.data.results` 区分 `created`、`already_added`、`completed`，回复列出数量。空选择没有领域写入，仍记录请求结果。
- 已有关联（含已完成待办）保留；未关联且已有 `status=completed` 的节点跳过。本票不创建独立完成状态；当前正式完成入口仍是既有待办完成操作。
- 每次有新增时路线版本增加一次。过期版本若全部已加入则返回零新增；若还包含待新增节点则整批拒绝并提示刷新重选，避免根据过期内容授权。并发单节点与批量操作共享事务逻辑。
- 聊天支持“把路线 完整标识 全部加入待办”，唯一完整标题也可解析；此批量指令的歧义标题或“把这条路线全部加入待办”等无稳定目标指令提示从面板选择。部分选择以面板为主。普通建议仍限当前会话，不因其标题含“路线”或“节点”而改走路线操作。
- 不修改已有待办、实现排期或独立节点完成流程。默认新增日期为空。

## 验收证据

确定性供应商响应只替换外部百炼/IQS 网络边界；公开 HTTP/SSE、实际临时 SQLite 和应用事务真实运行。本轮没有调用真实百炼/IQS，不声称已验证真实资料质量或大陆网络。

| 验收项 | 检查与结果 |
| --- | --- |
| 生成零新增、部分接受、跨会话补齐、重复确认 | `tests/test_roadmap_batch.py`：三节点路线选两项，重放原结果，重新打开数据库的新会话全选只补一项，再确认零新增；HTTP 回读日期和关联 |
| 空选择、非法节点、重复 ID、版本过期、已完成关联 | 同文件：空选择/非法批次路线不变，重复 ID 仅新增一次，旧版本拒绝，完成待办保持完成与原关联 |
| 聊天歧义与稳定身份 | 同文件：两条同名路线拒绝标题歧义，完整路线 ID 只接受目标路线；既有 `test_roadmaps.py` 保持普通建议会话隔离检查 |
| 并发防重 | 三会话两个批量请求与单节点接受竞争，HTTP 回读最终每个节点仅一条待办；浏览器双标签不同会话同时确认剩余节点 |
| 写入前/中途失败 | SQLite trigger 分别在第一、第二条 INSERT 前注入失败；第二条失败发生在第一条已写入但未提交之后。HTTP 观察到整批回滚，路线版本及关联不变，成功事件不存在；同请求重放原失败，显式重试成功 |
| 提交前后进程死亡、断线及重放 | `tests/test_roadmap_recovery.py`：批量请求排队、未执行时杀 Uvicorn；另一用例在收到已提交接受事件后立即断开 SSE 并杀进程。重启同库，前者零待办并可显式重试，后者三个待办且重放原终态；Last-Event-ID 续流不重复接受事件 |
| 浏览器完整主线 | `tests/roadmap_batch_browser.js`：零新增→暂不加入→刷新→选两项并核对清单→新增两项→刷新→全选剩余→双标签确认→三个待办→新会话重复确认零新增→刷新读回 |

浏览器使用 `tests.roadmap_demo:create_demo_app`，`ROADMAP_BATCH_DEMO=1`，隔离数据库 `output/issue21/browser.db`，端口 8101，Playwright 会话 `issue21`。截图 `output/playwright/issue21-preview.png` 与 `issue21-complete.png` 已目视检查。脚本需要一条三节点路线和零待办，在一次性测试数据库运行；不会清理已有数据。

## 检查历史

- 首个批量测试红：新 action 未接入，HTTP 422；实现后路线专项 32 passed。
- 首次 mypy 指出把单节点 `created: bool` 复用作计数，改用 `created_count` 后通过。
- 聊天批量测试先红：歧义指令落入模型失败；增加确定性路由后 33 passed。
- 批量及恢复专项最终 10 passed，2 条既有依赖弃用警告。恢复初跑 1 failed / 3 passed：测试误将排队中断后的既有 `failed` 协议写成 `partial`；修正断言后通过。不是应用恢复失败。
- 首次全量 244 passed，2 条既有警告，95.61 秒，`output/issue21/full-suite.txt`。
- 修复后的最终全量 **247 passed，2 条既有警告，96.49 秒**，`output/issue21/final-full-suite.txt`。没有失败或跳过。
- Standards 初审 0 项；Spec 初审发现 1 项 P2：宽泛聊天兜底误拦含“节点/路线”的普通建议标题。新增三条公开接口回归先 3 failed，再移除宽泛关键词拦截，仅由明确批量指令负责澄清；普通建议仍经过原有标题、模型目标与当前会话校验。补充新会话不能接受旧建议的断言及模糊路线指代澄清。
- mypy（17 个源文件）、Ruff check/format、前端 TypeScript/Vite build、`git diff --check` 已通过。浏览器最终控制台 0 errors / 0 warnings。

## Standards

独立只读审查 `043968d...9e25fff` 及修复增量 `9e25fff..705969d`：0 项可操作发现。对照项目规范、领域文档及 code-review smell baseline，未发现明确规范违例或值得修改的维护性问题。审查未重复执行全量。

## Spec

初审 1 项 P2（普通建议标题误拦），已在 `705969d` 修复。固定基线复审 **0 项剩余发现**，独立复跑 `test_roadmap_batch.py` 为 9 passed，2 条既有警告。确认原授权范围保持有效，未发现新增遗漏、错误实现或范围扩张。

两轴最终发现数：Standards 0，Spec 0。全量结果见上文，不以审查结论替代运行结果。

本轮按 implement 技能仅在当前 main 本地提交。已有未提交的 `CONTEXT.md`、`HANDOFF.md` 与 `docs/plans/` 保留，不纳入本次提交。
