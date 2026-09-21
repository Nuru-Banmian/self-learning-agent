# Issue #18：学习路线总规格组合验收

日期：2026-09-21。规格：[GitHub #18](https://github.com/Nuru-Banmian/self-learning-agent/issues/18)。固定审查基线 `93aa5b1`。六个实现切片 #19～#24 已合并关闭；本轮补充跨切片组合验证并加强路线生成约束。

**功能组合、公开接口、浏览器和真实服务流程已验证；真实生成内容质量仍为部分成功，不能将总规格标为全部验收完成。** 最新 Python 路线仍有练习调用实参不明确的问题，两个主题均未取得用户偏好的官方来源。提示词约束不构成语义正确性的保证。未推送、创建 PR 或关闭 #18。

## 本轮改动

- 主 Agent 搜索词聚焦本次目标必要的基础能力，减少检索附带高级主题。
- 路线组织明确运行环境、容器内 CLI、返回值类型、测试输入、过滤样例及可观察完成标准，避免直接照搬网页注释或无法证明的完成条件。
- `tests/test_roadmap_journey.py` 通过公开 HTTP/SSE 和真实临时 SQLite 串联记忆、澄清、跨会话续接、部分/全部加入、排期、完成、过期调整、拒绝同步、明确应用、防重与重启读回；后半段关闭模型和 IQS 凭据。
- `tests/roadmap_journey_browser.js` 验证同一路线上的选择、排期、完成与调整主线。
- `tests/live_roadmap_journey.py` 备份真实生成数据库，通过真实 Uvicorn、HTTP/SSE、百炼和 IQS 验证自然语言排期与调整，再终止并重启进程读回。不会修改用户日常数据库。

已有 `CONTEXT.md`、`HANDOFF.md` 和 `docs/plans/` 未提交内容原样保留；交接文档中的旧实现状态由本记录和既有切片验收记录补充，未覆盖用户文件。

## LR-01～LR-14 映射

下表的确定性用例仅模拟外部供应商，应用和 SQLite 真实运行。具体测试文件位于 `tests/`。

| 条件 | 本轮或既有回归入口 | 结果/限制 |
| --- | --- | --- |
| LR-01 自然学习意图及否定/引用/直接记录保护 | `test_roadmaps.py`；本轮双主题真实调用 | 流程通过；真实内容质量见下文 |
| LR-02 记忆、缺项追问、续接、临时范围 | `test_learning_requests.py`、`test_learning_request_recovery.py`、新增 journey | 确定性组合及浏览器续答通过；本轮真实双主题为完整需求输入 |
| LR-03 节点字段、顺序、实际来源、材料类型及缺口 | `test_roadmaps.py`；真实 `df12a18d81` | 结构/追溯通过；人工质量部分成功 |
| LR-04 生成不写待办、失败与持久恢复 | `test_roadmaps.py`、`test_roadmap_recovery.py`、journey | 确定性检查、真实双主题进程重启通过 |
| LR-05 明确范围、空日期、防重/并发 | `test_roadmap_batch.py`、journey | 通过，组合中先选一个再全选，不重复加入 |
| LR-06 跨会话路线与普通建议隔离 | `test_roadmaps.py`、`test_roadmap_batch.py` | 既有公开接口回归；组合验证跨会话读取维护 |
| LR-07 完成联动、无待办已掌握与完成跳过 | `test_roadmap_progress.py`、journey | 确定性、浏览器及真实数据组合通过 |
| LR-08 排期预览、确认、候选日期不等于加入 | `test_roadmap_schedule.py`、journey | 通过；浏览器先排未加入节点，后接受沿用日期 |
| LR-09 调整预览、拒绝及明确同步 | `test_roadmap_revisions.py`、journey | 确定性、浏览器及真实自然语言调整通过 |
| LR-10 完成快照、稳定身份、历史保留 | `test_roadmap_revisions.py`、journey | 既有历史节点回归；本轮组合保护已完成首节点 |
| LR-11 旧方案冲突、重复确认 | `test_roadmap_schedule.py`、`test_roadmap_revisions.py`、journey | 组合中另一会话完成后旧调整被拒绝，重新预览才能应用 |
| LR-12 中断、SSE 重连、显式重试 | 各 recovery 与 schedule/revisions 用例、journey | 自动化中含真实杀进程；本轮真实组合提交后重启读回，未额外在真实供应商调用中杀进程 |
| LR-13 升级与无供应商维护 | `verify_roadmap_upgrade.py`、journey | 通过，旧应用生成数据库升级后保留旧数据；无凭据完成排期/调整/进度维护 |
| LR-14 双主题真实材料和人工检查、页面组合 | 下列真实调用、人工表和浏览器记录 | 流程通过；质量部分成功，不作为总规格全绿依据 |

## 真实服务：所有尝试保留

复跑 `.venv/Scripts/python -m tests.live_roadmaps`，模型为 `qwen3.7-plus-2026-05-26`。每次隔离 SQLite、真实 Uvicorn/HTTP/SSE、真实百炼/IQS；原始输入、回复、来源正文、工具调用及终态保存在对应 `results.json`。

用户在本轮明确确认：电脑位于中国大陆，并已关闭代理/VPN。应用供应商客户端使用 `trust_env=False`。此为用户确认与客户端配置证据，未独立探测物理出口，亦不证明每个来源网站可在用户浏览器直接访问。旧双主题脚本固定输出 `unverified-in-this-run`，本记录补充用户确认，未改写原始文件。

| `output/issue19/live/` 目录 | Redis / Python 节点数 | 观察 |
| --- | --- | --- |
| `81fea66d93` | 4 / 5 | 原实现：Redis 文本/字节返回值及 NX 失败值断言错误、Docker CLI 环境未交代；Python 增加未要求的 send 进阶专题和不足以证明资源释放的标准。均为 partial，未隐藏失败 |
| `5b852137ba` | 4 / 4 | 首次提示约束修正后：容器 CLI、核心路线有所改善；Python 计数示例未明确 n，Redis TTL 过度依赖即时运行。仍为 partial |
| `df12a18d81` | 5 / 4 | 补充输入与计时约束后：两主题均实际搜索及读取正文，Redis 类型/容器/TTL 标准改善；Python 计数调用仍未指定实参。两条路线保存、防重复单项加入、进程重启读回通过；官方来源偏好未满足，如实标 partial |

本轮没有把旧记录中的成功结果充当新运行结果，也没有将后两轮替换掉首轮失败。每轮都执行了全部双主题脚本。

### 最新九节点人工阅读检查

依据 `df12a18d81/results.json` 的实际节点及保存材料。下面是内容阅读检查，**没有执行生成的 Redis/Docker 或 Python 练习**；测试中标记完成仅验证状态联动，不表示学习活动已完成。耗时都是模型估计，安装环境的 10 分钟不作保证。

| 节点 | 顺序与资料相关性 | 练习/完成标准检查 |
| --- | --- | --- |
| Redis 1 环境，10 分钟，S2 | 先准备 Docker、Redis、redis-py，再做读写；S2 为 Python 操作 Redis 教程 | 安装/启动前提明确，容器内 ping 返回 PONG；阅读通过，未执行 |
| Redis 2 连接与读写，5 分钟，S2 | 接环境，材料涉及客户端读写 | 明确 decode_responses=True，hello 文本预期一致；r 为练习需要创建的连接实例 |
| Redis 3 setex/TTL，5 分钟，S1/S2 | 由读写转过期，S1 为过期命令材料 | 给出键、值及 10 秒输入，以正数且不超过 10 的范围观察 TTL，阅读通过 |
| Redis 4 等待过期，5 分钟，S1/S2 | 紧接 setex | 等待 11 秒后 GET 为 None，标准有对应步骤 |
| Redis 5 封装缓存，5 分钟，S2 | 基于读写和过期组成最小闭环 | 明确 Alice、5 秒和等待 6 秒；首次命中及随后空值可观察 |
| Python 1 环境/测试日志，5 分钟，S2 | 先准备 Python 与三行文件 | 明确两行 ERROR、一行 INFO，为过滤提供正反样例 |
| Python 2 yield/next，8 分钟，S1/S2 | 先理解状态保持再读文件 | **部分**：描述 count_up(n) 却未指定调用实参；要观察 0、1 必须自行补充 n≥2。不能宣称可直接照抄 |
| Python 3 逐行读取，7 分钟，S2 | 接文件准备和 yield | with open、UTF-8、逐行 strip 与三行输出标准一致 |
| Python 4 日志过滤，10 分钟，S1/S2 | 组合读取与过滤 | 输入已有两行 ERROR 和一行 INFO，预期只输出两行匹配项；阅读通过 |

两个主题的来源均为第三方内容，未满足官方优先偏好；页面保留缺口，未冒充官方。Redis S1 正文有损坏片段，节点未要求运行该网页残缺代码。来源 ID 验证只证明出处存在，不是技术正确性验证。

### 真实组合：`output/issue18/live/19afad39bd/results.json`

```powershell
.venv/Scripts/python -m tests.live_roadmap_journey --saved-db output/issue19/live/df12a18d81/live.db --mainland-confirmed
```

从最新真实双主题库备份，Redis 原有一个关联待办；全选后仍为五条 Redis 关联项，另保留一条 Python 待办。真实自然语言“从明天开始、每天1小时30分钟”生成排期；预览不改待办，确认才应用。完成首节点后，实际再次调用百炼与 IQS，调整第二节点为容器内 CLI SET/GET；其余节点、日期及首节点完成事实保持不变。拒绝同步不写入，明确同步后持久保存，真实进程重启后读回一致。

该调整有一个实际搜索调用。人工检查应区分局部调整与整体课程修订：用户要求保留其他节点，因此后续 Python 练习仍存在；调整结果不是重新验证过的完整课程。CLI 新节点的命令及 hello 预期可读，未运行练习。

## 浏览器与升级

使用 `tests.learning_request_demo:create_demo_app`、`output/issue18/browser.db`、8098 端口，供应商模拟，应用和页面真实运行。聊天保存 Python 基础 → 简短 Redis 请求仅追问目标 → 刷新/新会话续答 → 打开路线链接 → 运行 `tests/roadmap_journey_browser.js`。部分接受、排期预览/刷新/确认、再接受第二节点沿用日期、已掌握联动、拒绝调整/明确同步、新会话刷新均通过。截图 `output/playwright/issue18-journey.png` 已目视检查，日志 `output/issue18/browser-journey.txt`。

首次驱动脚本生成后直接等待详情而未点击路线链接，30 秒超时；快照显示已生成路线且有列表链接。补上点击后完成全部主线，此为驱动错误，不隐去或描述为应用失败。截图展示的是模拟资料。

旧库升级复验：`.venv/Scripts/python -m tests.verify_roadmap_upgrade`，输出 `output/issue19/upgrade/ddcdc0b0da`。从 `8878ff9` 的旧应用通过 HTTP 创建真实旧库，再用当前应用读取、升级并加入路线，通过；没有用新版本 SQL 伪造旧库。

## 检查与审查

专项 `test_roadmap_journey.py` 初次通过（1 passed），是已有能力的组合验收，不声称先红后绿。随后路线+组合专项 **32 passed**，两条既有 Starlette/AnyIO 弃用警告。提示约束的失败证据来自真实模型输出，不写断言提示词文本的伪回归测试。

本轮 mypy（21 源文件）、Ruff check/format（94 文件）、前端 TypeScript/Vite build、`git diff --check` 均通过。全量与独立审查结果完成后补记。
