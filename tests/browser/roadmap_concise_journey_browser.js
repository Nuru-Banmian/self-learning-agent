/* Run after roadmap_concise_browser.js on the isolated concise_roadmap_demo DB.
   This journey intentionally retries, creates one todo and completes it via UI. */
async (page) => {
  const base = await page.evaluate(() => location.origin);
  const session = await page.evaluate(() => localStorage.getItem("assistant-session"));
  const read = async path => (await page.request.get(`${base}/api${path}`)).json();
  const saved = await read(`/sessions/${session}`);
  const latest = await read(`/runs/${saved.run_ids.at(-1)}`);
  if (!latest.roadmap_context || latest.roadmap_links.length || !latest.retryable) throw new Error("Seed a retryable learning result with no route");
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: /^学习路线/ }).click();
  const outcome = page.getByRole("region", { name: "学习请求处理结果" });
  await outcome.getByText("本轮未得到可打开的路线", { exact: false }).waitFor();
  await outcome.getByRole("button", { name: "查看处理结果 →", exact: true }).click();
  const record = page.locator(`#run-${latest.id}`);
  await record.waitFor();
  if (!(await record.innerText()).includes(latest.content)) throw new Error("Result link opened another request");
  if (!await record.locator("details").evaluate(detail => detail.open)) await record.locator("details > summary").click();
  if (await record.locator("details > p").textContent() !== latest.reply) throw new Error("Failure reply unavailable");
  await record.getByRole("button", { name: "重试未完成处理", exact: true }).click();
  await page.waitForFunction(async ({ base, id }) => {
    const run = await (await fetch(`${base}/api/runs/${id}`)).json();
    if (!run.retry_run_id) return false;
    const child = await (await fetch(`${base}/api/runs/${run.retry_run_id}`)).json();
    return child.status !== "running" && child.status !== "queued";
  }, { base, id: latest.id });
  await page.reload();
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: /^学习路线/ }).click();
  await outcome.waitFor();
  await page.screenshot({ path: "output/issue34/browser/no-route-retry-390.png", fullPage: true });
  const routes = await read("/roadmaps");
  const route = await read(`/roadmaps/${routes.find(item => item.status !== "partial").id}`);
  const node = route.nodes[0];
  await page.getByRole("navigation", { name: "路线列表" }).locator(`a[href="#roadmap-${route.id}"]`).click();
  if ((await read("/todos")).length) throw new Error("Expected no fixture todos before confirmation");
  await page.locator(`#node-${node.id}`).getByRole("checkbox").check();
  if ((await read("/todos")).length) throw new Error("Selection wrote a todo");
  await page.getByRole("button", { name: "确认加入所选 1 项", exact: true }).click();
  await page.locator(`#node-${node.id}`).getByRole("checkbox", { name: "已加入待办", exact: true }).waitFor();
  let todos = await read("/todos");
  if (todos.length !== 1 || todos[0].roadmap.node_id !== node.id || todos[0].roadmap.id !== route.id) throw new Error("Wrong node accepted");
  await page.locator(`#node-${node.id}`).getByRole("button", { name: "标记已掌握（同步完成待办）", exact: true }).click();
  await page.getByText("学习进度：已完成 1 / 2， 剩余 1 个节点", { exact: true }).waitFor();
  todos = await read("/todos");
  if (todos.length !== 1 || todos[0].status !== "completed") throw new Error("Completion did not synchronize");
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: "待办", exact: true }).click();
  await page.getByRole("group", { name: "筛选待办" }).getByRole("button", { name: /^已完成/ }).click();
  await page.getByRole("region", { name: "待办列表" }).getByRole("button", { name: todos[0].title, exact: true }).click();
  await page.locator(`a[href="#roadmap-${route.id}/${node.id}"]`).click();
  if (await page.locator(`#node-${node.id}`).evaluate(element => element !== document.activeElement)) throw new Error("Todo link did not focus exact node");
  await page.locator(`#node-${node.id} .roadmap-node-details > summary`).click();
  await page.locator(`#node-${node.id} .roadmap-source > summary`).click();
  await page.locator(`#node-${node.id}`).getByText(/^完成记录/).click();
  const current = await read(`/roadmaps/${route.id}`);
  if (!current.nodes[0].completion) throw new Error("Completion record absent");
  await page.screenshot({ path: "output/issue34/browser/node-details-progress-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "output/issue34/browser/node-details-progress-1440.png", fullPage: true });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error("Expanded details overflow");
  }
  return { retry: (await read(`/runs/${latest.id}`)).retry_run_id, route_id: route.id, node_id: node.id, todos: todos.length, completed: current.progress.completed, checks: "visible no-route failure, exact processing result, retry, explicit selection/confirmation, progress, todo deep link, full source and completion detail" };
}
