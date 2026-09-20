# Issue tracker: GitHub

本项目的需求、规格和开发任务记录为 GitHub Issues，使用 `gh` CLI 操作。

## 仓库定位

通过 `git remote -v` 确认目标仓库；在已关联的仓库中，`gh` 可自动识别。
尚未配置远程时，先取得用户指定的 `owner/repo`，再通过 `--repo owner/repo` 指定目标。

## 常用操作

- 创建：`gh issue create --title "标题" --body-file <正文文件>`
- 读取：`gh issue view <编号> --comments`；需要结构化字段时使用 `--json` 获取正文、标签和评论。
- 列表：`gh issue list --state open --json number,title,body,labels,comments`；按需添加标签或状态筛选。
- 评论：`gh issue comment <编号> --body-file <评论文件>`
- 添加标签：`gh issue edit <编号> --add-label "<标签>"`
- 移除标签：`gh issue edit <编号> --remove-label "<标签>"`
- 关闭：`gh issue close <编号> --comment "关闭原因"`

多行正文先写入 UTF-8 文件，再通过 `--body-file` 传入。

技能要求“发布到 issue tracker”时，创建 GitHub Issue。
技能要求“获取相关 ticket”时，读取对应 Issue 及其评论。

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub Issues 和 PR 共用编号空间。引用仅有 `#编号` 时，先确认对象类型，再使用对应的 `gh issue` 或 `gh pr` 操作。

## Wayfinder 约定

仅在使用 `wayfinder` 时采用以下规则：

- Map：一个带 `wayfinder:map` 标签的 Issue，保存 Notes、Decisions-so-far 和 Fog。
- 子任务：通过 GitHub sub-issue 关联到 Map；不可用时，在 Map 中维护任务列表，并在子任务正文顶部写 `Part of #<Map编号>`。
- 类型标签：`wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`。
- 依赖：优先使用 GitHub 原生 Issue dependencies；调用依赖接口时使用阻塞 Issue 的数据库 ID。不可用时，在正文顶部写 `Blocked by: #<编号>`。全部阻塞项关闭后才能开始。
- 选择：只考虑属于该 Map、仍开放、无未关闭阻塞项且未分配负责人的子任务，按 Map 顺序选择第一个。
- 认领：开始任务时先用 `gh issue edit <编号> --add-assignee @me` 分配给当前开发者。
- 完成：评论记录结果，关闭子任务，并在 Map 的 Decisions-so-far 中追加结果摘要和链接。
