/* playwright-cli run-code input. Use a disposable browser and an isolated DB
   containing at least one saved roadmap. No model or search calls are made. */
async (page) => {
  const base = await page.evaluate(() => location.origin);
  const response = await page.request.get(`${base}/api/roadmaps`);
  if (!response.ok()) throw new Error("Cannot read saved roadmaps");
  const routes = await response.json();
  if (!routes.length) throw new Error("Seed a saved roadmap before this check");

  async function assertRoutes() {
    const links = page.getByRole("navigation", { name: "路线列表" }).getByRole("link");
    await links.nth(routes.length - 1).waitFor({ timeout: 5000 });
    if (await links.count() !== routes.length) throw new Error("Missing saved roadmaps");
    for (const route of routes) {
      if (await links.filter({ hasText: route.title }).count() !== 1)
        throw new Error(`Missing route: ${route.title}`);
    }
  }

  await page.evaluate(() => localStorage.clear());
  await page.goto(base);
  await assertRoutes();
  const firstSession = await page.evaluate(() => localStorage.getItem("assistant-session"));
  await page.getByRole("button", { name: "＋ 新会话", exact: true }).click();
  await page.waitForFunction((old) => localStorage.getItem("assistant-session") !== old, firstSession);
  await assertRoutes();
  await page.reload();
  await assertRoutes();

  /* An obsolete browser session takes the same new-session loading path. */
  await page.evaluate(() => localStorage.setItem("assistant-session", "missing-session"));
  await page.reload();
  await assertRoutes();
  if (await page.evaluate(() => localStorage.getItem("assistant-session")) === "missing-session")
    throw new Error("Missing-session recovery did not create a session");
}
