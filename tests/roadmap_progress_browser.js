/* playwright-cli run-code --filename; isolated three-node fixture on port 8102. */
async (page) => {
  const base = page.url().split("/").slice(0, 3).join("/");
  const route = page.getByRole("article", { name: "路线详情", exact: true });
  await page.getByRole("checkbox", { name: "选择节点 1：练习 Redis 字符串读写", exact: true }).check();
  await page.getByRole("checkbox", { name: "选择节点 2：验证 Redis 缓存过期", exact: true }).check();
  await page.getByRole("button", { name: "确认加入所选 2 项", exact: true }).click();
  await page.getByRole("heading", { name: "已保存的待办 2", exact: true }).waitFor();
  await route.getByText("学习进度：已完成 0 / 3， 剩余 3 个节点", { exact: true }).waitFor();
  const todo = page.getByRole("complementary", { name: "待办列表" }).getByRole("listitem").filter({ has: page.getByRole("heading", { name: "练习 Redis 字符串读写", exact: true }) });
  await todo.getByRole("button", { name: "标记完成", exact: true }).click();
  await route.getByText("学习进度：已完成 1 / 3， 剩余 2 个节点", { exact: true }).waitFor();
  await route.getByRole("region", { name: "节点 2", exact: true }).getByRole("button", { name: "标记已掌握（同步完成待办）", exact: true }).click();
  await route.getByText("学习进度：已完成 2 / 3， 剩余 1 个节点", { exact: true }).waitFor();
  await page.getByRole("heading", { name: "已完成 2", exact: true }).waitFor();
  const second = await page.context().newPage();
  await second.goto(page.url());
  await second.getByRole("button", { name: "＋ 新会话", exact: true }).click();
  // Competing completion requests from independent sessions preserve one fact.
  await Promise.all([
    route.getByRole("region", { name: "节点 3", exact: true }).getByRole("button", { name: "标记已掌握（不创建待办）", exact: true }).click(),
    second.getByRole("region", { name: "节点 3", exact: true }).getByRole("button", { name: "标记已掌握（不创建待办）", exact: true }).click(),
  ]);
  await route.getByText("学习进度：已完成 3 / 3， 剩余 0 个节点", { exact: true }).waitFor();
  await second.getByText("学习进度：已完成 3 / 3， 剩余 0 个节点", { exact: true }).waitFor();
  await second.close();
  await page.reload();
  await route.getByText("学习进度：已完成 3 / 3， 剩余 0 个节点", { exact: true }).waitFor();
  const routes = await (await page.request.get(`${base}/api/roadmaps`)).json();
  const current = await (await page.request.get(`${base}/api/roadmaps/${routes[0].id}`)).json();
  const todos = await (await page.request.get(`${base}/api/todos`)).json();
  if (todos.length !== 2 || todos.some(t => t.status !== "completed")) throw new Error("Inconsistent todos");
  if (current.nodes.some(n => !n.completion) || current.nodes[2].todo_id !== null) throw new Error("Missing completion or unwanted todo");
  const run = await (await page.request.get(`${base}/api/runs/${current.nodes[2].completion.run_id}`)).json();
  const retry = await (await page.request.post(`${base}/api/runs/${run.id}/retry`)).json();
  if (retry.id !== run.id) throw new Error("Committed completion was retried");
  for (const button of await route.getByRole("button", { name: /标记已掌握/ }).all()) {
    if (await button.isEnabled()) throw new Error("Completed node is still enabled");
  }
  await route.getByRole("region", { name: "节点 3", exact: true }).getByText(/完成记录 ·/).click();
  await page.screenshot({ path: "output/playwright/issue22-progress.png", fullPage: true });
  console.log("PASS: todo completion, linked/unlinked mastery, concurrent sessions, reload, committed retry; 3/3 nodes, 2 completed todos");
}
