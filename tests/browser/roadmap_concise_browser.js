/* playwright-cli run-code input. Open the app in a disposable browser with an
   isolated DB and select a session containing >= 2 assistant roadmap links.
   This check reads real public HTTP responses and changes only browser UI state. */
async (page) => {
  const base = await page.evaluate(() => location.origin);
  const session = await page.evaluate(() => localStorage.getItem("assistant-session"));
  const sessionResponse = await page.request.get(`${base}/api/sessions/${session}`);
  if (!sessionResponse.ok()) throw new Error("Select the seeded session first");
  const saved = await sessionResponse.json();
  const messages = saved.messages.filter(message => message.role === "assistant" && message.roadmap_links?.length);
  const linked = messages.flatMap(message => message.roadmap_links.map(link => ({ message, link })));
  const distinct = [...new Map(linked.map(item => [item.link.roadmap_id, item])).values()];
  if (distinct.length < 2) throw new Error("Seed two distinct roadmap results in this session");

  async function noOverflow() {
    const dimensions = await page.evaluate(() => ({ width: window.innerWidth, content: document.documentElement.scrollWidth }));
    if (dimensions.content > dimensions.width + 1) throw new Error(`Horizontal overflow: ${JSON.stringify(dimensions)}`);
  }
  async function openResult({ message, link }) {
    await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: "对话", exact: true }).click();
    const href = `#roadmap-${link.roadmap_id}${link.node_id ? `/${link.node_id}` : ""}`;
    await page.locator(`#message-${message.id}`).locator(`a[href="${href}"]`).click();
    const response = await page.request.get(`${base}/api/roadmaps/${link.roadmap_id}`);
    if (!response.ok()) throw new Error("Linked roadmap is unreadable");
    const route = await response.json();
    await page.getByRole("article", { name: "路线详情" }).getByRole("heading", { name: route.display_title || route.title, exact: true }).waitFor();
    if (await page.locator(".roadmap-nodes > li").count() !== route.nodes.length) throw new Error("Node order lost entries");
    for (const [index, node] of route.nodes.entries()) {
      const card = page.locator(".roadmap-nodes > li").nth(index);
      if (await card.locator(".roadmap-node").getAttribute("id") !== `node-${node.id}`) throw new Error("Node order changed");
      if (!(await card.innerText()).includes(node.display_goal || node.goal)) throw new Error("Action goal missing");
      if (await card.locator(".roadmap-node-details").evaluate(detail => detail.open)) throw new Error("Node details expanded by default");
      if (await card.getByText(`练习：${node.exercise}`, { exact: true }).isVisible()) throw new Error("Exercise visible by default");
    }
    if (await page.locator(".roadmap-tools[open], .roadmap-history[open]").count()) throw new Error("Forms or history expanded by default");
    if (await page.locator(".roadmap-nodes input:checked").count()) throw new Error("Selection leaked between roadmaps");
    await noOverflow();
    return route;
  }

  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.reload();
    const latest = saved.run_ids.at(-1);
    const latestResponse = await page.request.get(`${base}/api/runs/${latest}`);
    const run = await latestResponse.json();
    if (run.roadmap_context && run.research?.status) {
      await page.getByRole("navigation", { name: "工作台导航" }).getByRole("link", { name: "对话", exact: true }).click();
      const research = page.locator(".research-disclosure");
      await research.waitFor();
      if (await research.evaluate(detail => detail.open)) throw new Error("Roadmap research expanded by default");
    }
    const first = await openResult(distinct[0]);
    const eligible = first.nodes.find(node => !node.todo_id && node.status !== "completed");
    if (eligible) await page.locator(`#node-${eligible.id}`).getByRole("checkbox").check();
    const second = await openResult(distinct[1]);
    const node = second.nodes.find(item => item.source_ids.length);
    if (!node) throw new Error("Seed a node with a real recorded source");
    const card = page.locator(`#node-${node.id}`);
    await card.locator(".roadmap-node-details > summary").click();
    if (await page.locator(".roadmap-node-details[open]").count() !== 1) throw new Error("Opening one node expanded others");
    if (!(await card.innerText()).includes(node.exercise)) throw new Error("Full exercise unavailable");
    const source = second.sources.find(item => item.id === node.source_ids[0]);
    const detail = card.locator(".roadmap-source").first();
    await detail.locator("summary").click();
    if (await detail.getByRole("link").getAttribute("href") !== source.url) throw new Error("Source link differs from saved evidence");
    const text = await detail.innerText();
    if (!text.includes(source.snippet)) throw new Error("Saved source snippet missing");
    if (!text.includes(source.material_type === "body" ? "已取得正文" : "仅搜索摘要")) throw new Error("Source provenance changed");
    if (source.body_truncated && !text.includes("截取片段")) throw new Error("Truncation state hidden");
    await noOverflow();
    await card.locator(".roadmap-node-details > summary").click();
    await page.screenshot({ path: `output/issue34/roadmap-default-${width}.png`, fullPage: true });
  }
  return { widths: [1440, 390], routes: distinct.slice(0, 2).map(item => item.link.roadmap_id), checks: "persisted message links, ordering, collapsed details and forms, independent source detail, selection isolation, no horizontal overflow" };
}
