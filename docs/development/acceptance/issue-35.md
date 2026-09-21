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

- 主体集成后的早期全量回归为 **387 passed**，187.32 秒；该结果早于模型专用结构和末次路由修复，不作为最终 HEAD 结果。
- 将最终修改覆盖到从已提交 HEAD 导出的隔离快照后运行 `python -m pytest -q`：**398 passed**，2 条既有 FastAPI/Starlette 弃用警告，184.94 秒。隔离快照不包含工作区原有文档与测试目录重排，证明最终实现不依赖这些未提交改动。
- 末次路由修复聚焦回归：`tests/test_scheduling_removed.py`、`tests/test_self_paced_dates.py`、`tests/test_roadmap_batch.py`、`tests/test_roadmap_details.py`，**56 passed**。覆盖肯定内容调整与否定日期混合表达、明确节点加入防绕路、普通标题含“学习路线节点说明”、否定节点加入后的普通路由，以及节点详情普通问答。
- 覆盖无时间的两个主题、相关记忆、不伪造基础、旧时间待续需求、免补文字指定继续、多需求隔离、重复提交/重试、全部与部分加入、旧计划节点加入、A/B 日期兼容、内容修改、同步授权、陈旧方案、日期变更拒绝、完成与重启。
- 旧排期用例改为停用和历史兼容检查；保留普通日期、内容调整、并发和真实进程重启回归，没有仅隐藏按钮或删除日期测试。
- Ruff、格式检查、mypy（23 个 `app` 文件）、前端 TypeScript/Vite 构建及 `git diff --check` 均通过。

## 审查结果

以 `7196fda1f67abad7b870ec1395b79bacde8586c9` 为固定点完成 Standards 与 Spec 双轴复核。

- Standards：无明文规范违反；保留一项非阻断 P3 建议，即普通待办目标识别在 `schedule_chat.py` 与 `revision_chat.py` 存在相似逻辑，后续可按实际需要统一，本任务不扩大重构。
- Spec：首次复核发现节点加入兜底会误拦普通待办标题及否定分句。修复后，明确的节点加入/安排仍被引导到无日期节点入口；普通“记录明天整理学习路线节点说明”可保存；否定节点加入不再被路线面板吞掉，而继续服从固定点之前已有的普通待办保守授权。最终复核无新增可操作问题。

## 浏览器与真实服务

可复演入口：

```powershell
$env:DB_PATH='output/issue35/browser.db'
.venv\Scripts\python.exe -m uvicorn tests.support.self_paced_demo:create_demo_app --factory --port 8845
npx --yes --package @playwright/cli playwright-cli -s=issue35 open http://127.0.0.1:8845
npx --yes --package @playwright/cli playwright-cli -s=issue35 run-code --filename tests/browser/roadmap_self_paced_browser.js
.venv\Scripts\python.exe -m tests.live.live_self_paced_learning
```

浏览器夹具提供历史计划 A、实际日期 B、旧排期/日期调整及两个纯时间待续需求。浏览器操作、HTTP/SSE、SQLite 和真实进程重启均为真实；模型与搜索供应商为模拟。桌面 1440px 与手机 390px 的完整主线通过，截图和 `browser-results.json` 保存在 `output/playwright/issue35/`。确认后两个新待办日期为空，实际待办日期与历史计划日期分别保留；完成同步、跨会话读回、免填文字继续指定需求及同库重启前后业务对象相等均通过。

真实百炼/IQS 使用独立数据库，凭据仅从本地配置读取，不访问用户业务数据库。初次证据保存在 `output/issue35/live/4fe0bc55b2/`，包含真实请求、SSE、路由、查询、来源、持久化结果与逐节点人工检查。

两主题无时间生成、真实百炼/IQS 调用、来源留存、自然语言排期拒绝、无日期节点加入、普通手动日期和无密钥重启读回均成功。最终普通用户措辞的内容调整实际经过模型预览与明确确认，保留实际待办日期 `2030-01-09`、历史计划日期和真实来源；证据见 `final-revision-results.json`。这些结果证明功能路径可用，不等同于课程内容质量通过。

## 最终验收结论

**功能验证通过；Issue #35 不能宣称完整验收通过，因为 SP-15 真实生成内容质量仍未通过。**

- Redis 首次结果存在客户端安装缺失和跨节点 TTL 矛盾。窄提示词修复后的复验改善了安装与 TTL，但 `update_data` 只打印新值并删除缓存，`mock_db_query` 始终返回旧固定内容，因此无法满足“重新读取 New Data”的完成标准；已有缓存也使首次 miss 不能保证。
- Python 生成器路线的后续文件调用早先函数时缺少定义或导入，尚无成功复验证据。
- 人工检查没有实际执行 Docker/Redis 练习，大陆实际出口位置也未独立核验。原始失败和复验失败均保留在 `output/issue35/live/4fe0bc55b2/`，没有通过重复抽样掩盖缺口。

因此，本轮可确认 SP-01～SP-14 相关功能行为及 SP-15 的真实调用、持久化和调整链路；不能确认 SP-15 要求的逐节点可执行内容质量。

## 提交边界

本次只提交 #35 的末次路由修复、公开回归测试和本验收文档。已有文档/测试目录重排及其他未提交改动继续保留；未推送、创建 PR、合并或关闭 Issue。
