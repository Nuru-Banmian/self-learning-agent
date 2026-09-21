/* playwright-cli run-code. Use an isolated server with ROADMAP_BATCH_DEMO=1,
   one saved three-node route, and zero todos. External providers are fixtures. */
async (page) => {
  const base = "http://127.0.0.1:8101";
  const routes = await (await page.request.get(`${base}/api/roadmaps`)).json();
  if (routes.length !== 1) throw new Error("Expected one isolated route");
  const url = `${base}/#roadmap-${routes[0].id}`;
  async function count(expected) {
    const todos = await (await page.request.get(`${base}/api/todos`)).json();
    if (todos.length !== expected) throw new Error(`Expected ${expected} todos, got ${todos.length}`);
    if (todos.some((t) => t.scheduled_date !== null)) throw new Error("Unexpected schedule");
  }
  await count(0);
  await page.goto(url);
  await page.getByRole("button", { name: "确认加入所选 0 项", exact: true }).waitFor();
  if (await page.getByRole("button", { name: "确认加入所选 0 项", exact: true }).isEnabled())
    throw new Error("Empty selection enabled");
  await page.getByRole("link", { name: "暂不加入（保留路线）", exact: true }).click();
  await page.reload();
  await count(0);
  await page.getByRole("navigation", { name: "路线列表" }).getByRole("link").click();
  await page.getByRole("checkbox", { name: "选择节点 1：练习 Redis 字符串读写", exact: true }).check();
  await page.getByRole("checkbox", { name: "选择节点 2：验证 Redis 缓存过期", exact: true }).check();
  const preview = page.getByRole("region", { name: "确认加入清单", exact: true });
  if (await preview.getByRole("listitem").count() !== 2) throw new Error("Preview is not two items");
  if ((await preview.innerText()).includes("验证 Redis 缓存缺失")) throw new Error("Unselected item in preview");
  await preview.screenshot({ path: "output/playwright/issue21-preview.png" });
  await count(0);
  await page.getByRole("button", { name: "确认加入所选 2 项", exact: true }).click();
  await page.getByRole("heading", { name: "已保存的待办 2", exact: true }).waitFor();
  await count(2);
  await page.reload();
  await page.getByRole("button", { name: "全部选择未加入节点", exact: true }).click();
  await page.getByRole("button", { name: "确认加入所选 1 项", exact: true }).waitFor();
  const second = await page.context().newPage();
  await second.goto(url);
  await second.getByRole("button", { name: "＋ 新会话", exact: true }).click();
  await second.getByRole("button", { name: "全部选择未加入节点", exact: true }).click();
  await Promise.all([
    page.getByRole("button", { name: "确认加入所选 1 项", exact: true }).click(),
    second.getByRole("button", { name: "确认加入所选 1 项", exact: true }).click(),
  ]);
  await page.getByRole("heading", { name: "已保存的待办 3", exact: true }).waitFor();
  await second.getByRole("heading", { name: "已保存的待办 3", exact: true }).waitFor();
  await count(3);
  await second.close();
  await page.getByRole("button", { name: "＋ 新会话", exact: true }).click();
  await page.getByRole("textbox", { name: "你的安排或问题", exact: true }).fill(`把路线 ${routes[0].id} 全部加入待办`);
  await page.getByRole("button", { name: "发送 ↑", exact: true }).click();
  await page.getByText("本次新增 0 项（未安排）；已加入 3 项，跳过已完成 0 项。未选节点保持原状。", { exact: true }).first().waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "已保存的待办 3", exact: true }).waitFor();
  await count(3);
  await page.getByRole("article", { name: "路线详情", exact: true }).screenshot({ path: "output/playwright/issue21-complete.png" });
  console.log("PASS: 0 -> 2 -> 3, defer/preview/reload, concurrent tabs and new-session duplicate acceptance");
}
