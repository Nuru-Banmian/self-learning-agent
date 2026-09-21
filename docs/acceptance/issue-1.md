# Issue #1：首版总规格补验

日期：2026-09-21。本轮起点与固定审查基线：`8196b06c76c2513f6e18bb2438bf885141360175`。GitHub 读回 #2～#9 均 CLOSED，#1 OPEN。子任务关闭不代表总规格所有验收已通过。

## 本轮修复

1. 规格原始演示输入“我看技术资料喜欢先看官方文档，再做一个小练习。今天我要学 Python 生成器。”原先没有保存记忆或待办，转入搜索且因缺 IQS 返回 partial。现在完整保留顺序偏好，将独立日期意图保存为当天待办。学习候选只能使用原文完整分句；不丢弃后半步骤、相邻期限或原文否定授权。
2. 和风官方 API Host 示例现在包含多级子域，旧配置只接受一级子域而拒绝合法配置。现在保留并接受完整专属域名，继续拒绝外部后缀、用户信息、协议、路径及空标签，凭据不会因此被发送到任意域名。
3. 新增 [API 配置说明](../api-setup.md)，记录控制台入口、独立凭据、完整 Host 及真实验收命令。未改用户 `.env` 或日常数据库。

Host 格式依据于本日读取的 [和风官方文档](https://dev.qweather.com/docs/configuration/api-host/)。

## 红绿回归与真实调用

- 原始示例公开 HTTP/SSE 回归：初次 **1 failed、1 passed**，失败为未保存偏好。修复后学习/记忆维护/待办/证据聚焦 **83 passed**。
- 多级 Host 公开天气路径：初次 **1 failed、1 passed**，失败为配置验证拒绝官方示例。修复并加入错误目标检查后，天气与证据聚焦 **55 passed**。
- 中间 Ruff 检查发现新增长行和1个文件格式问题，均已修正；未掩盖为初次就通过。
- 真实百炼独立示例：`output/issue1/spec-before.json` 记录修复前 partial、0记忆、0待办；`spec-after.json` 记录 completed、1条完整偏好、1项当天待办。模型额外提出的1条无效候选被拒绝，不能表述为所有候选均通过。未调用真实搜索或天气。
- 组合入口 `python -m tests.live_acceptance --mock-information --output output/issue1/live` 已改为规格原始示例。`output/issue1/live/da5dc3fcaf/` **passed**：固定真实百炼模型、模拟 IQS/和风；实际启动3次进程、重启2次，核验新会话新主题、纠正/删除、仅建议不写入、显式接受防重复、完成读回、天气消歧/超范围/失败和检查点重启读回。模式、调用次数与耗时见 results.json，各轮证据与请求结构单独保存。
- 不带模拟参数的完整入口 `output/issue1/live/85015edd13/` **skipped**：本地缺 `IQS_API_KEY`、`QWEATHER_API_KEY`、`QWEATHER_API_HOST`；没有请求真实信息服务。不得按进程退出码0将此项计为通过。

## 浏览器

用独立 `output/issue1/browser.db`、真实百炼和真实后端，在浏览器输入规格原始示例。显示1项当天待办及1条完整偏好，刷新后仍存在；公开接口再次读回各1条。此场景不需要搜索或天气。

- 快照：`output/playwright/issue1-saved.yml`、`issue1-reloaded.yml`。
- 已查看截图：`issue1-reloaded.png`、`issue1-memory.png`，原文顺序偏好未截断，列表日期为2026-09-21。
- 数据读回：`output/issue1/browser-readback.json`；浏览器控制台0 errors、0 warnings。
- 未重新执行 #9 已记录的双标签、断网和窄屏流程；它们仍属于历史证据。

## Standards

独立只读审查 `8196b06…b622729`：文档规范违反0项，可操作启发式异味0项。角色、领域术语、公开 HTTP/SSE 与隔离 SQLite 的测试边界及真实/模拟区分符合既有规范。

## Spec

独立只读审查同一差异：可操作代码问题0项，未发现越界。审查者复验天气与证据 **55 passed**。来源完整性、范围期限及完整原文授权未被分句修复绕过。真实信息服务和大陆网络条件仍是未完成的规格要求。

## 剩余交付条件

沿用 [Issue #9 的 AC-01～AC-12 映射](issue-9.md)。本轮补齐了原始示例和合法 Host 的代码缺口，但 **AC-09/AC-10 真实 IQS、和风及 AC-12 大陆无代理联调尚未通过，首版完整交付未达成**。

后续需在本地配置独立凭据及 Host，确认实际大陆网络和代理/VPN状态，执行真实搜索、结果网站访问、城市与天气及两主线组合测试，记录实际结果后再判断 #1 是否满足关闭条件。本轮 implement 授权本地实现和当前 main 提交；未推送、创建 PR、合并或关闭 Issue。

## 最终门禁

基于实现提交 `b622729`，审查后全量测试一次：**187 passed，2 warnings，61.24秒**。警告为既有 Starlette/httpx 和 anyio 弃用提示。mypy（14个应用源文件）、Ruff check、Ruff format（36文件）、前端 TypeScript/Vite build 和 `git diff --check` 均通过。之后仅更新文档，不改变应用与测试行为。

Standards 0项；Spec 0项可操作代码发现。真实服务缺少配置为 skipped，大陆网络为 unverified；两者不计为通过。
