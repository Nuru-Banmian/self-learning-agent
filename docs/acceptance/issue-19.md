# Issue #19：学习路线验收记录

日期：2026-09-21。规格：[Issue #19](https://github.com/Nuru-Banmian/self-learning-agent/issues/19)，父规格 [#18](https://github.com/Nuru-Banmian/self-learning-agent/issues/18)。审查固定基线：`8878ff91a50c108d187da43be3d7fb6a0a45ab56`。

实现已覆盖本票的搜索路线、持久内容、跨会话读取及单节点接受。确定性接口、进程恢复及浏览器检查通过；真实百炼/IQS 双主题生成、单节点防重和重启读回已有证据，但资料取得和人工内容质量只能记为 **部分成功**。不把以下记录等同于所有生成练习均可直接执行或大陆网络完整验收通过。

本轮仅在 `main` 本地提交；未推送、创建 PR、合并或关闭 Issue。原有未提交 `CONTEXT.md`、`HANDOFF.md`、`docs/plans/` 不属于本次提交，也未被覆盖。

## 范围与入口

- 示例：“我想学习 Redis，有 Python 基础，目标是实现缓存，每次可投入30分钟”。无须额外说“搜索”；否定搜索、引用解释、直接记录待办有保护。学习子主题排除和补充完成标准要求不会取消整条路线。
- `GET /api/roadmaps` 和 `GET /api/roadmaps/{id}` 读取跨会话路线及来源。稳定路线/节点标识、顺序、目标、预计分钟、练习、完成标准及候选待办持久保存。
- 节点接受复用 `POST /api/sessions/{id}/messages`，action 为 `accept_roadmap_node`，arguments 为 `roadmap_id`、`node_id`、`expected_version`。聊天可用“把节点 完整标识 加入待办”；裸“加进去”不猜目标。
- 生成及“暂不加入”不创建待办；只接受所选单节点，默认日期为空。页面显示未安排，可从待办跳回完整节点。
- 路线、节点关联、执行终态、事件和 `result_committed` 在同一个 `Store.finish` 事务中提交。普通行动建议仍限当前会话。
- 多轮澄清、批量选择、节点完成进度、排期、调整留给 #20～#24。

## 逐项映射

| #19 验收项 | 证据与观察 | 结果 |
| --- | --- | --- |
| 信息充分学习意图实际搜索；否定/引用/解释不机械转路线 | `test_roadmaps.py` 正负参数化测试，供应商输入记录证明搜索到达边界；真实两主题都有 IQS 调用。补充普通研究工具绕过否定的防护 | 确定性通过；真实原样例有调用 |
| 稳定身份、顺序、每节点完整字段、单个候选、真实来源类型 | HTTP 返回与重启前后比较；引用不存在的 S999 时拒绝路线；真实 8 节点人工检查如下 | 结构/追溯通过；语义质量部分成功 |
| 未确认不改待办；单节点默认无日期；待办可打开节点 | HTTP 比较生成前后待办；单节点接受/重放；模拟浏览器和真实保存数据浏览器均验证 | 通过 |
| 刷新、重启、新会话路线列表及详情；普通建议会话隔离 | HTTP、真实 Uvicorn 进程重启；浏览器初访、旧 session 失效、新会话、刷新；普通建议跨会话接受被拒绝 | 通过 |
| 原子保存、重放及重复接受不重复；已提交 partial 不再生成 | 同 request 重放、不同 request 重复接受、双会话并发、陈旧版本、提交前后杀进程、SSE Last-Event-ID；学习阶段失败后路线保持已提交 | 通过 |
| empty/error/正文失败/组织失败保留资料与缺口 | 参数化失败注入、伪来源、超时及有界扩大检索；已有资料保留，无资料不伪造路线/待办 | 确定性通过；真实失败保留 |
| 公开 HTTP/SSE、真实 SQLite、浏览器、双主题真实调用与人工检查 | 下列分层证据；不混用模拟与真实结果 | 流程通过；真实资料/内容质量部分成功 |

## 确定性与进程验证

所有确定性用例只替换百炼/IQS 等外部供应商的网络响应；应用、HTTP/SSE、事务和临时 SQLite 实际运行。`test_roadmap_recovery.py` 真正启动/终止 Uvicorn；不是仅重新构造 Store。

```powershell
.venv/Scripts/python -m pytest tests/test_roadmaps.py tests/test_roadmap_recovery.py -q
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m mypy
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m ruff format --check .
npm --prefix frontend run build
git diff --check
```

- 交接时全量：207 passed，2 warnings，75.88 秒，`output/issue19/full-suite.txt`。这是早期实现结果，不代表最终版本。
- 本次接手专项：26 passed，2 warnings，12.59 秒，`output/issue19/resume-focused.txt`。
- 审查修复后的路线专项：31 passed，2 warnings，7.12 秒，`output/issue19/resume-roadmaps-final-2.txt`。
- `c9fd124` 启动的中间全量：219 passed，2 warnings，78.66 秒，`output/issue19/resume-full-suite.txt`。最后一次意图统一修复后另跑最终全量，结果见下方最终检查。
- 警告来自 Starlette TestClient 的 httpx 与 AnyIO 弃用提示，未被隐藏。

### 旧版本数据库升级

运行 `.venv/Scripts/python -m tests.verify_roadmap_upgrade`。脚本通过 `git archive` 导出基线应用，在单独进程中用旧应用的 HTTP 接口创建待办和偏好记忆，并保存会话、消息和执行记录的公开快照；随后当前应用打开同一 SQLite，逐个比较旧字段，允许新增字段。再生成新路线、接受单节点、重新打开应用读回。

结果：**通过**，原有一条待办和一条记忆以及来源/历史均保留，初始路线列表为空，新增路线及一条无日期待办可重启读回。证据 `output/issue19/upgrade/65097fcc0e/{before.json,result.json,memory.db}`。只模拟供应商，不是手写新版本 SQL 冒充旧库。

## 浏览器验证

交接阶段使用 `tests.roadmap_demo:create_demo_app`、隔离 `output/issue19/browser.db`：完整输入→路线→暂不加入→新会话→单节点加入→刷新→待办跳回。外部供应商全部模拟。截图 `output/playwright/issue19-node.png`。断网接受第二节点时正确显示连接中断，联网后重试同一请求并刷新只保留两条待办；预期出现 `ERR_INTERNET_DISCONNECTED`，不是全程零 console 错误。

本次使用真实联调数据库副本 `output/issue19/live-browser.db`，`app.main:create_app`、8097 端口、独立浏览器 `issue19-resume`。操作不会改动原始真实联调数据库。本次页面阶段没有重新调用百炼或 IQS，仅读取此前实际取得的资料和手动接受节点。

1. 清空 localStorage 的新访客出现空路线，公共 API 中仍有两条路线；红色断言 `Saved roadmaps missing on first visit` 保留在 `fresh-browser-red.txt`。
2. `newSession()` 复用 `refresh(created.id)` 后，首次加载即显示两条路线，不必先聊天；沿用 `currentSession`/`refreshVersion` 防迟到响应。绿色证据 `fresh-browser-green.txt`。
3. Python 路线可逐项展开资料，正文/摘要标识及缺口可见。截图 `issue19-live-material-expanded.png` 已目视检查；正文长文本换行正常。来源是已保存的第三方真实查询结果。
4. 接受 Python 节点 2，待办从 2 变为 3，新项日期为 null；按钮显示已加入。新会话及刷新仍可查看；从该待办跳回节点，显示相同学习内容。`live-browser-acceptance.txt`、`issue19-live-accepted.png`。
5. 人为设置不存在的 session 后自动创建新会话，立即加载两条路线。该失败注入产生 `/sessions/missing-session` 及 suggestions 两个预期 404；不宣称全程零错误。

可复跑的初访回归输入为 `tests/roadmap_first_visit.js`。先用隔离数据库准备至少一条路线并启动应用，浏览器必须是专门的测试会话（脚本会清理该浏览器的 localStorage）：

```powershell
npx --yes --package @playwright/cli playwright-cli -s=issue19-check open http://127.0.0.1:8097
$browserCheck = (Get-Content tests/roadmap_first_visit.js -Raw) -replace '\r?\n', ' '
npx --yes --package @playwright/cli playwright-cli -s=issue19-check run-code $browserCheck
```

该脚本检查初访、新会话、刷新及失效会话回退；最终通过记录 `output/issue19/first-visit-regression-final.txt`。截图均在 `output/playwright/`，其余浏览器日志在 `output/issue19/` 与 `.playwright-cli/`。

## 真实百炼/IQS：保留每一次结果

复跑入口 `.venv/Scripts/python -m tests.live_roadmaps`，使用已有本地配置，不打印凭据；每次独立输出目录、真实 Uvicorn/HTTP/SSE/SQLite。模型快照 `qwen3.7-plus-2026-05-26`，代理 `trust_env=False`。原始 run、事件、工具请求与结果、记忆使用、路线均在各目录 `results.json`。本次接手重新读取了这些文件，沿用已完成的联调证据，没有为相同验收再次付费调用。

| 目录（`output/issue19/live/`） | Redis / Python 节点数 | 观察 |
| --- | --- | --- |
| `9f2dab3836` | 4 / 4 | 都是 partial；官方偏好未满足、正文有失败；人工发现环境准备与残缺代码问题，后续加强组织约束 |
| `4849962a88` | 5 / 3 | 都是 partial；人工发现多余进阶节点及不可靠的生成器资源释放标准，后续收紧必要目标/可观察断言 |
| `820d92d507` | 4 / 0 | Python 定向搜索 empty，无路线，明确记录该主题联调失败；重启读回一条路线不代表双主题通过 |
| `79465c7614` | 4 / 4 | 两条路线与研究均 partial；单节点跨会话接受、防重及真实进程重启读回两条路线通过 |

最新每个主题有 4 次实际工具请求：定向 IQS search 空结果→预算内扩大 search→两份 read_page（一份成功、一份失败）。查询确实携带“优先阅读官方资料”的偏好。Redis S1 worktile 正文取得，S2 php.cn 正文失败；Python S1 php.cn 正文失败，S2 CSDN 正文取得；其余保存为摘要。都没有把第三方资料冒充官方。

### 最新八节点人工检查

下表依据 `79465c7614/results.json` 中实际保存的节点与材料。人工阅读检查不等于执行过每条练习；Redis 服务/CLI 与 Python 练习均未作为用户实践任务逐条运行。

| 节点 | 相关性、顺序与耗时 | 可执行性与完成标准检查 |
| --- | --- | --- |
| Redis 1 环境与依赖，30 分钟，S1 | 首先准备服务和 Python 客户端，顺序合理；S1 主要讲过期命令，安装步骤依据较弱 | **部分**：Docker 分支未说明宿主机安装 redis-cli 或使用容器内 CLI；不能保证照抄即成功 |
| Redis 2 SETEX/TTL，30 分钟，S1 | 接环境准备，资料与过期命令相关 | **部分**：依赖节点1的 CLI；GET 过期检查可观察，但 TTL 放在过期之后不能观察倒计时变化 |
| Redis 3 redis-py，30 分钟，S2 摘要 | 由 CLI 迁移到 Python，目标相关 | **部分**：默认返回 bytes，标准要求打印字符串 Alice 却未写解码或 decode_responses 配置 |
| Redis 4 缓存缺失，30 分钟，S1/S2 | 接写入读取，再处理过期后的 None，顺序合理 | 逻辑可检查；循环练习未指定等待间隔，未实际运行，不能证明所有环境直接可用 |
| Python 1 环境/5行日志，10 分钟，S2 | 提供后续日志输入，顺序合理 | 数据条件明确，检查文件与 Python 版本；阅读通过，未执行 |
| Python 2 yield/next，20 分钟，S2 | 先理解暂停恢复再读文件 | 三次 next 对应三次 yield，可观察 print 时机；阅读通过，未执行 |
| Python 3 逐行读取，25 分钟，S2/S5 | 基于前两步，材料相关 | with open 与逐行 yield 可实现，完成标准和操作匹配；阅读通过，未执行 |
| Python 4 ERROR 过滤，25 分钟，S1/S2 | 在读取后加入过滤，目标相关 | 排除空行/注释并筛选 ERROR，标准和输入对应；阅读通过，未执行 |

官方文档偏好：两个主题均 **未满足**，缺口已在路线/页面明确展示。取得来源 ID 只证明可追溯，不证明材料正确、资料足够或练习技术细节完整。未独立核验本轮大陆物理出口，也未逐个访问来源网站证明大陆直达；API 成功和禁用代理不能替代这项验证。

## 失败与修复历史

- 交接前 generation/acceptance 测试先红后绿；恢复夹具未创建造成两次启动失败；一条测试曾错误要求 retry_run_id 不变，按既有显式重试契约修正。此前记录保留于交接与原输出。
- 前轮 Spec 发现固定句首和全局关键词误伤，在 `d76c0f4` 修复；带修饰语的否定搜索遗漏在接手工作树中，验证后提交 `96aba41`。
- 首次浏览器路线空态已复现，`22c8487` 修复。最初浏览器脚本使用相对 API URL 报 Invalid URL，是验证脚本错误；随后绝对 URL 断言真正复现应用缺陷。
- 本轮 Spec 两项 P2：子主题否定/说明附加句误伤、直接“记一条”被误转路线。新增用例 **3 failed / 10 passed** 后修复；又发现“制定学习路线”入口与“想学习”不一致，**1 failed / 6 passed** 后统一检测。日志 `review-intent-red.txt`、`explicit-route-red.txt` 保留。
- 同时验证普通 `research_learning` 可绕过否定搜索（基线已有问题），两个新增测试先 **2 failed**，随后在共同工具入口增加否定保护，日志 `negative-research-red.txt`。
- 浏览器 CLI 期间有过期 ref、脚本多行注释 SyntaxError、受限执行环境无 URL 构造器 ReferenceError，调整脚本后复跑成功；这些是测试驱动错误，不作为应用故障。`first-visit-regression.txt`、`first-visit-regression-green.txt` 保留实际失败，文件名不代表通过。
- 真实搜索/正文/内容问题均在上表保留，没有覆盖旧目录或只展示最好一次。

## 最终检查与双轴审查

最终全量 **220 passed，2 warnings，77.53 秒**，`output/issue19/final-full-suite.txt`；mypy 检查 16 个源文件通过；Ruff check 通过，format check 70 个文件通过；前端 TypeScript/Vite build 通过；`git diff --check` 通过。初访浏览器脚本通过，受保护文件哈希与接手时一致。

双轴固定审查 `8878ff9...8654fc2`，不使用不断移动的 main 作为基线。Standards 最终 **0 项可操作发现**，只读审查代码、项目规范及文档，未独立重跑测试。Spec 最终 **0 项剩余可操作发现**，独立重跑路线测试 **31 passed，2 warnings**，并核对真实原始记录、升级结果、浏览器及全量日志。

历史：截至 `96aba41`，Standards 0 项、Spec 2 项 P2；`c9fd124` 关闭直接记录问题并补两工具否定保护，正向意图仍有同根遗漏；`d8cb202` 统一正向入口。Spec 最终确认原两项 P2 均关闭。双轴无剩余代码发现不等于真实资料/练习质量全绿；上文 partial 和未验证边界保持不变。本段是审查后回填结果，未改应用代码。
