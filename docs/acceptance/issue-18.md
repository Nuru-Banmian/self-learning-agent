# Issue #18：学习路线总规格组合验收

日期：2026-09-21。规格：[GitHub #18](https://github.com/Nuru-Banmian/self-learning-agent/issues/18)。固定审查基线 `93aa5b1`。六个实现切片 #19～#24 已合并关闭；本轮补充跨切片组合验证并加强路线生成约束。

**功能组合、公开接口、浏览器和真实服务流程已验证；真实生成内容质量仍为部分成功，不能将总规格标为全部验收完成。** 第四轮 Python 的调用实参问题未复现，但 Redis 漏报了官方偏好缺口，容器检查使用了未保证适用于 PowerShell 的 grep；真实局部调整还留下后续 Python 连接初始化缺口。提示词约束不构成语义正确性的保证。未推送、创建 PR 或关闭 #18。

## 本轮改动

- 主 Agent 搜索词聚焦本次目标必要的基础能力，减少检索附带高级主题。
- 路线组织明确运行环境、容器内 CLI、返回值类型、测试输入、过滤样例及可观察完成标准，避免直接照搬网页注释或无法证明的完成条件。
- 收尾保留 `Node.exercise` 的完整调用与确切实参要求。应用在当前请求或生效偏好涉及官方资料时追加官方身份未核实提示，持久保存为 partial；搜索标题或模型声称不能作为官方身份凭据。该策略保守，即使实际 URL 属于官方，也不宣称程序已完成身份核验。
- 前序练习被替换或移出且后续练习原文保留时，调整预览追加先修依赖未核验提示；确认后合并至路线缺口，保持后续节点原文。此为不确定性提示，不是依赖解析器，也不证明生成课程已修复。调整提示词同时要求修改节点保留后续所需初始化。
- 修复资料 partial 与记忆保存失败同时发生时漏报 `memory_learning` 的组合情况，不覆盖已有错误。
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
| LR-03 节点字段、顺序、实际来源、材料类型及缺口 | `test_roadmaps.py`；真实 `df12a18d81` / `f3c69fb853` | 结构/追溯通过；人工质量部分成功 |
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
| `f3c69fb853` | 3 / 4 | 字段描述加强后的第四轮：两主题均 partial，单项防重和两路线重启读回通过。Python 生成器改成完整无参调用；Redis 未明确提示官方偏好未满足。此为修复前真实证据，未改写结果 |
| `334b75bfac` | 3 / 4 | 收尾代码第五轮：两主题均 partial，应用官方身份提示在真实回复和保存路线中出现；单项防重及两路线重启读回通过。Python 再出现平台命令和组合输出问题，见下文 |

本轮没有把旧记录中的成功结果充当新运行结果，也没有用后续轮次替换首轮失败。每轮都执行了全部双主题脚本。

### 第三轮九节点人工阅读检查（保留原结论）

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

### 第四轮七节点人工阅读检查

依据 `output/issue19/live/f3c69fb853/results.json`，本次重新阅读保存节点、来源 URL、材料类型和 gaps；不是重新调用供应商，也未执行练习。

| 节点 | 阅读结论 |
| --- | --- |
| Redis 1 Docker / redis-py 准备 | Docker 安装启动、容器内 PING 有说明；`docker ps \| grep my-redis` 未提供 PowerShell 等价命令，跨平台可执行性部分满足 |
| Redis 2 Python 读写 | 完整 `redis.Redis(..., decode_responses=True)`、set/get、确切字符串断言，输入与类型相符 |
| Redis 3 过期缓存 | 独立脚本重新创建连接，setex 5 秒、等待 6 秒、None 预期相符；即时读值仍依赖运行时未经历超过 TTL 的停顿 |
| Python 1 测试日志 | 三行 INFO/ERROR/INFO，正反样例齐全 |
| Python 2 yield / next | `simple_gen()` 完整定义与调用，依次产出 1、2；第三轮 n 实参缺失在本次未复现 |
| Python 3 逐行读取 | 完整 `read_lines('test.log')` 调用、UTF-8 和三行输出标准 |
| Python 4 ERROR 过滤 | 在上一节点函数基础上给出 `filter_errors('test.log')`，预期仅输出 `ERROR: Disk full` |

两个主题均为第三方来源。Python 明示未取得官方文档；Redis 只说明 S2 正文失败与 S4/S5 不相关，**没有明确说明官方偏好未满足，LR-03 在该真实记录中有漏报**。收尾公开接口回归模拟模型返回空 gaps 及转载标题，验证应用仍显示官方身份尚未核实，并测试当前例外不会重新附加旧官方偏好；不把此模拟证据写成第四轮真实输出已修正。

### 第五轮：收尾代码真实复验及人工阅读

`output/issue19/live/334b75bfac/results.json`、`output/issue18/live-fifth.txt`。仍为真实百炼/IQS、隔离库、Uvicorn/HTTP/SSE，两个主题都实际取得材料并保存，防重及进程重启读回通过。两条路线的 gaps 均含应用追加的官方身份未核实提示；来源仍全为第三方，未解决官方来源获取质量。

| 节点 | 阅读结论（均未执行练习） |
| --- | --- |
| Redis 1 Docker | 安装启动前提、容器内 PING 与 PONG 对应；本次未再用 grep |
| Redis 2 redis-py | pip 安装与交互 import 检查明确 |
| Redis 3 缓存 | 独立完整脚本，默认 bytes 对应 `b'abc-123'`，setex 5 秒/等待 6 秒/None 一致；即时首次命中受实际执行时序影响 |
| Python 1 测试日志 | 明确生成 200 行 INFO/ERROR 样例；`wc -l` 未保证适用于纯 PowerShell，质量部分满足 |
| Python 2 生成器 | 完整无参 `simple_gen()`、两次 next 与 1/2 对应，原 n 实参问题未复现 |
| Python 3 逐行读取 | 完整文件调用；只打印前三行但循环仍遍历全文件，不能说只读取了三行；未实测内存指标 |
| Python 4 过滤 | 要求“在 log_reader.py 基础上增加过滤逻辑”，若保留上一节点的顶层打印循环，会先输出含 INFO 的前三行，违反“每一行都包含 ERROR”的标准。未明确替换旧调用段，**新增可执行组合缺口** |

第五轮模型还写“核心 API 用法已通过多份资料交叉验证”；这是模型文案，不是独立技术验证证据。新提示已在真实生成路径验证，但生成练习语义与平台兼容性仍有必要缺口，LR-14 保持部分满足。没有追加真实调整调用来证明新调整提示词能保留初始化，原真实 CLI 调整缺口仍未修复。

### 真实组合：`output/issue18/live/19afad39bd/results.json`

```powershell
.venv/Scripts/python -m tests.live_roadmap_journey --saved-db output/issue19/live/df12a18d81/live.db --mainland-confirmed
```

从第三轮真实双主题库备份，Redis 原有一个关联待办；全选后仍为五条 Redis 关联项，另保留一条 Python 待办。真实自然语言“从明天开始、每天1小时30分钟”生成排期；预览不改待办，确认才应用。完成首节点后，实际再次调用百炼与 IQS，调整第二节点为容器内 CLI SET/GET；其余节点、日期及首节点完成事实保持不变。拒绝同步不写入，明确同步后持久保存，真实进程重启后读回一致。

该调整有一个实际搜索调用。**发现具体依赖缺口**：第二节点原负责创建 Python Redis 连接 `r`，改为 CLI 后不再初始化 `r`，保留的第 3～5 节点仍调用 `r.setex/r.get`，按调整后顺序执行会缺少前置准备。原请求明确第二节点“不写 Python 代码”并保留其他节点，不能擅自重写它们；原始真实路线未修复，也未自动迁移旧库。收尾新增预览风险提示及生成约束，但不能把它报告为这份课程已修好。CLI 新节点命令及 hello 预期经阅读，练习未执行。

## 浏览器与升级

使用 `tests.learning_request_demo:create_demo_app`、`output/issue18/browser.db`、8098 端口，供应商模拟，应用和页面真实运行。聊天保存 Python 基础 → 简短 Redis 请求仅追问目标 → 刷新/新会话续答 → 打开路线链接 → 运行 `tests/roadmap_journey_browser.js`。部分接受、排期预览/刷新/确认、再接受第二节点沿用日期、已掌握联动、拒绝调整/明确同步、新会话刷新均通过。截图 `output/playwright/issue18-journey.png` 已目视检查，日志 `output/issue18/browser-journey.txt`。

首次驱动脚本生成后直接等待详情而未点击路线链接，30 秒超时；快照显示已生成路线且有列表链接。补上点击后完成全部主线，此为驱动错误，不隐去或描述为应用失败。截图展示的是模拟资料。

旧库升级复验：`.venv/Scripts/python -m tests.verify_roadmap_upgrade`，输出 `output/issue19/upgrade/ddcdc0b0da`。从 `8878ff9` 的旧应用通过 HTTP 创建真实旧库，再用当前应用读取、升级并加入路线，通过；没有用新版本 SQL 伪造旧库。

## 检查与审查

专项 `test_roadmap_journey.py` 初次通过（1 passed），是已有能力的组合验收，不声称先红后绿。随后路线+组合专项 **32 passed**，两条既有 Starlette/AnyIO 弃用警告。提示约束的失败证据来自真实模型输出，不写断言提示词文本的伪回归测试。

收尾检查（按代码版本区分）：

- 新偏好提示的公开接口测试先失败，原结果为 completed/success 且无缺口；修复后通过。扩大专项初次为 **31 passed / 1 failed**，暴露已有记忆失败提示仅覆盖 completed 的问题；保留 `output/issue18/preference-red.txt`、`preference-green.txt`，后者虽然命名 green，内容实际含一次失败，不能称通过。修复组合错误后路线/调整/journey 专项 **71 passed**；随后扩展的三种偏好情形（生效记忆、当前要求、当前视频例外）**3 passed**。
- 先修依赖提示公开接口测试先失败、修复后通过，证据 `dependency-red.txt` / `dependency-green.txt`。验证预览不写原节点、确认才合并缺口、后续节点保留和无供应商重启读回；不验证练习语义。
- `352ba11` 全量 **325 passed，2 warnings，149.52 秒**，`output/issue18/final-suite.txt`。两条为既有 Starlette/AnyIO 弃用警告。早前 **321 passed** 在最终字段描述及提示修复前，不能代替此结果。
- 明确否定修复后的中间全量 **326 passed，2 warnings，150.61 秒**，`final-suite-after-review.txt`；该次测试启动后又补充了正向但否定视频场景，不能作为 `dc41274` 的最终全量。
- 最终应用代码 `dc41274` 全量 **327 passed，2 warnings，148.57 秒**，`output/issue18/final-suite-dc41274.txt`；代码在该轮运行中未改动。随后仅更新本验收文档。
- mypy **21 源文件**、Ruff check、Ruff format **95 文件**、前端 TypeScript/Vite build 均通过，分别保存在 `final-mypy.txt` / `final-ruff.txt` / `final-format.txt` / `final-build.txt`。在 `dc41274` 上重跑 mypy/Ruff/format 也通过，见三个 `final-*-after-review.txt`；前端未变化，沿用同一 build。中间曾有新增断言超长，format 后解决；文档编辑曾产生 CRLF 差异，已统一本轮文档为 LF，`git diff --check` 通过。
- Playwright 收尾补验使用 `tests.roadmap_demo:create_demo_app` 与 `output/issue18/final-browser/roadmaps.db`、8099 端口，供应商模拟。真实页面显示官方身份提示；手工改第一节点练习、预览出现先修依赖提示，确认并刷新后路线仍保留两项缺口。文本快照 `final-browser-route.txt` / `final-browser-preview.txt` / `final-browser-confirmed.txt`，截图 `output/playwright/issue18-final-warning.png` 已目视检查。该截图仅证明提示可见，不证明示例练习完整可运行。
- 受保护 `CONTEXT.md`、`HANDOFF.md` 的 SHA-256 与交接记录一致；`docs/plans/` 文件清单及哈希与本次开始快照一致。仅显式暂存本轮文件，未纳入以上用户内容。

### Standards

固定基线 `93aa5b1`，独立代理审查 `git diff 93aa5b1...HEAD`，并复核增量至 `dc41274`，**0 项可操作发现**。未发现文档标准硬违规或需要修改的基线代码异味。测试使用公开边界，风险提示未引入额外解析抽象；各类证据和未验证部分区分清楚。代理仅阅读，未运行测试。

### Spec

初审发现 **1 项 P2 代码缺陷**：当前明确“这次不要官方资料，只看视频”仍因原文含“官方”被追加缺口，违反当前要求优先及 LR-03。新增公开接口回归先红（1 failed / 3 passed），修复为仅考虑未引用、未否定的当前官方要求及生效偏好；路线专项 **35 passed**。证据 `output/issue18/review-preference-red.txt` / `review-preference-green.txt`。修复后独立复核关闭原 P2。随后自查正向“优先官方资料但不要视频”被宽泛否定条件误伤，新增回归先红（1 failed / 4 passed），改为否定仅作用于官方资料短语，路线专项 **36 passed**；保留 `review-positive-red.txt` / `review-positive-green.txt`。Spec 代理独立复跑五种偏好情形 **5 passed、31 deselected、2 warnings**，至 `dc41274` 无剩余可操作代码问题或无关扩展。

另外保留 **1 类必要验收缺口（LR-14 部分满足）**：真实来源偏好、生成练习平台兼容性/组合正确性，以及真实 CLI 调整后的初始化缺口。新增提示不是课程修复，第四/第五轮未复现 n 缺参也不构成普遍质量保证。代理独立阅读原始节点并用供应商模拟公开接口复现 P2，没有执行生成练习。

最终双轴结论：**Standards 0 项，Spec 0 项剩余代码发现 + 1 类必要内容验收缺口**。只在当前 main 本地提交，未推送、创建 PR 或关闭 Issue #18。未执行生成的学习练习，未重新验证旧真实调整课程已修复；LR-14 保持部分满足。
