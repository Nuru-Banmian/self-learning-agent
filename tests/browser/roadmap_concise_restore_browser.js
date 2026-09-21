/* Run after restarting the isolated server with its existing SQLite file. */
async (page) => {
  await page.reload();
  const base = await page.evaluate(() => location.origin);
  const session = await page.evaluate(() => localStorage.getItem("assistant-session"));
  const saved = await (await page.request.get(`${base}/api/sessions/${session}`)).json();
  const message = saved.messages.find(item => item.role === "assistant" && item.roadmap_links?.length);
  const link = message.roadmap_links[0];
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: "对话", exact: true }).click();
  const routeLink = page.locator(`#message-${message.id}`).getByRole("link");
  if (await routeLink.getAttribute("href") !== `#roadmap-${link.roadmap_id}`) throw new Error("Persisted message route association lost on restart");
  await routeLink.click();
  await page.getByText("学习进度：已完成 1 / 2， 剩余 1 个节点", { exact: true }).waitFor();
  if (await page.locator(".roadmap-node-details[open]").count()) throw new Error("Reload expanded node details");
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: "对话", exact: true }).click();
  await page.getByRole("button", { name: "＋ 新会话", exact: true }).click();
  await page.waitForFunction(old => localStorage.getItem("assistant-session") !== old, session);
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: /^学习路线/ }).click();
  await page.getByRole("navigation", { name: "路线列表" }).locator(`a[href="#roadmap-${link.roadmap_id}"]`).click();
  await page.getByText("学习进度：已完成 1 / 2， 剩余 1 个节点", { exact: true }).waitFor();
  if (await page.locator(".roadmap-node-details[open]").count()) throw new Error("New session expanded node details");
  const todos = await (await page.request.get(`${base}/api/todos`)).json();
  if (todos.length !== 1 || todos[0].status !== "completed") throw new Error("Read-only restoration changed business state");
  await page.evaluate(id => localStorage.setItem("assistant-session", id), session);
  await page.reload();
  return { session, route_id: link.roadmap_id, checks: "process restart, exact persisted reply route link, concise default, completed progress, new-session route recovery without writes" };
}
