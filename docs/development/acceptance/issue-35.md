# Issue #35：自主节奏学习路线验收

日期：2026-09-21。范围：[Issue #35](https://github.com/Nuru-Banmian/self-learning-agent/issues/35)，父规格 [#33](https://github.com/Nuru-Banmian/self-learning-agent/issues/33)。审查固定点为用户确认的 `7196fda1f67abad7b870ec1395b79bacde8586c9`。

## 实现结果

- 学习需求只澄清必要目标和基础；时间原话可留存，但不作为生成门槛。旧时间问题通过只读投影移除，查看及重启不会自动生成路线。公开 `continue_learning` 可省略内容，只继续明确选择的需求。
- 节点单项和批量加入均创建无日期待办；重复加入、已完成跳过、版本与关联保护继续有效。普通有日期待办和明确手动改期继续保留。
- 路线排期入口停用。旧结构化预览、确认、恢复和未提交请求重试返回 `failed` / `roadmap_scheduling_removed`；旧已完成或主结果已提交的部分完成请求幂等读回。历史方案、日期及完成事实保留。
- 非日期调整省略日期表示保留；兼容原样日期字段。新增、更改、清空日期或确认含日期变化的旧方案，整体拒绝。历史计划 `planned_date` 与关联待办的实际日期分别保留；内容调整不写日期。
- 页面保留简洁路线、详情与来源、选择确认、内容调整及进度。排期仅可查历史，含日期变化的旧调整无法确认。

## 确定性验证

所有业务断言通过公开 HTTP/SSE 与真实临时 SQLite 读回；旧版状态及中断由隔离数据库夹具构造。供应商模拟仅用于确定性回归，不冒充真实服务。

- 全量回归：`python -m pytest -q`，**387 passed**，187.32 秒。后续针对真实调用发现的模型结构问题与普通待办标题歧义，另做聚焦复验；最终结果补记于下方。
- 覆盖无时间的两个主题、相关记忆、不伪造基础、旧时间待续需求、免补文字指定继续、多需求隔离、重复提交/重试、全部与部分加入、旧计划节点加入、A/B 日期兼容、内容修改、同步授权、陈旧方案、日期变更拒绝、完成与重启。
- 旧排期用例改为停用和历史兼容检查；保留普通日期、内容调整、并发和真实进程重启回归，没有仅隐藏按钮或删除日期测试。
- 格式、Ruff、mypy、前端 TypeScript/Vite 构建和差异检查另行执行并记录最终结果。

## 浏览器与真实服务

可复演入口：

```powershell
$env:DB_PATH='output/issue35/browser.db'
.venv\Scripts\python.exe -m uvicorn tests.support.self_paced_demo:create_demo_app --factory --port 8845
npx --yes --package @playwright/cli playwright-cli -s=issue35 open http://127.0.0.1:8845
npx --yes --package @playwright/cli playwright-cli -s=issue35 run-code --filename tests/browser/roadmap_self_paced_browser.js
.venv\Scripts\python.exe -m tests.live.live_self_paced_learning
```

浏览器夹具提供历史计划 A、实际日期 B、旧排期/日期调整及两个纯时间待续需求。浏览器操作与 SQLite 真实，夹具供应商为模拟。截图保存在 `output/playwright/issue35/`。

真实百炼/IQS 使用独立数据库，凭据仅从本地配置读取，不访问用户业务数据库。初次证据保存在 `output/issue35/live/4fe0bc55b2/`，包含真实请求、SSE、路由、查询、来源、持久化结果与逐节点人工检查。

两主题无时间生成与来源留存已成功；不将生成成功等同于练习质量通过。初次 Redis 练习存在安装步骤和跨节点 TTL 错误，Python 生成器练习存在跨文件函数缺少导入的问题。针对安装、共享状态和导入约束补充了提示词并进行针对性复验；所有原始失败证据保留。SP-15 的最终质量状态及后续证据见补记，未通过部分不得宣称已验收。大陆实际出口位置未独立核验。

## 提交边界

本次只提交 #35 实现、相关测试和本验收文档。已有文档/测试目录重排及其他未提交改动继续保留，未发布远程变更、创建 PR 或关闭 Issue。
