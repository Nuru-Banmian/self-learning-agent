/* playwright-cli run-code input. Use a fresh tests.support.self_paced_demo
   server. Providers are simulated; browser actions and HTTP/SSE/SQLite are real.
   Create output/playwright/issue35 before running. Mutates only the disposable fixture. */
async (page) => {
  const base = await page.evaluate(() => location.origin);
  const readJson = async (path) => {
    const response = await page.request.get(`${base}${path}`);
    if (!response.ok()) throw new Error(`Read failed: ${path}`);
    return response.json();
  };
  const initialRoutes = await readJson('/api/roadmaps');
  const summary = initialRoutes.find(item => item.title === 'Redis 缓存入门');
  if (!summary || initialRoutes.length !== 1) throw new Error('Use a fresh self-paced fixture');
  const id = summary.id;
  const url = `${base}/#roadmap-${id}`;
  const read = () => readJson(`/api/roadmaps/${id}`);
  const todos = () => readJson('/api/todos');
  const route = page.getByRole('article', { name: '路线详情', exact: true });
  const revision = page.getByRole('region', { name: '路线调整', exact: true });
  const nav = page.getByRole('navigation', { name: '工作台导航' });
  const initial = await read();
  const [first, second, third] = initial.nodes;
  const initialPending = (await readJson('/api/learning-requests')).filter(item => !item.roadmap_id);
  if (initialPending.length !== 2 || initialPending.some(item => item.questions.length)) throw new Error('Legacy time questions still block reads');
  if (first.planned_date !== '2026-10-01' || first.todo.scheduled_date !== '2026-10-09') throw new Error('Historical A / actual B fixture absent');
  const submitted = [];
  async function perform(trigger) {
    const responsePromise = page.waitForResponse(response => response.request().method() === 'POST' && /\/api\/sessions\/[^/]+\/messages$/.test(response.url()));
    await trigger();
    const response = await responsePromise;
    if (response.status() !== 202) throw new Error(`Action rejected at HTTP ${response.status()}`);
    const payload = response.request().postDataJSON();
    submitted.push(payload);
    const runId = (await response.json()).id;
    const stream = await page.request.get(`${base}/api/runs/${runId}/events`);
    if (!(await stream.text()).includes('event: terminal')) throw new Error('Missing terminal SSE event');
    const run = await readJson(`/api/runs/${runId}`);
    if (!['completed', 'partial'].includes(run.status)) throw new Error(`Action failed: ${JSON.stringify(run)}`);
    await page.waitForFunction(() => localStorage.getItem('assistant-pending') === null);
    return { run, payload };
  }
  async function openRoute() {
    await page.goto(url);
    await page.reload();
    await route.getByRole('heading', { name: initial.display_title || initial.title, exact: true }).waitFor();
    await page.waitForFunction(() => localStorage.getItem('assistant-pending') === null);
  }
  async function openRevision() {
    const disclosure = page.locator('.roadmap-tools').filter({ has: page.getByText(/^调整学习路线(?: · 有待确认方案)?$/, { exact: true }) });
    if (!await revision.isVisible()) await disclosure.locator(':scope > summary').click();
  }
  async function noOverflow() {
    const size = await page.evaluate(() => ({ width: innerWidth, content: document.documentElement.scrollWidth }));
    if (size.content > size.width + 1) throw new Error(`Horizontal overflow: ${JSON.stringify(size)}`);
  }
  const business = value => JSON.stringify({ nodes: value.nodes, history: value.history_nodes, progress: value.progress });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await openRoute();
    if (await route.locator('input[type="date"]').count()) throw new Error('Route still contains date inputs');
    if (await route.getByRole('button', { name: /排期|清空日期/ }).count()) throw new Error('Schedule write control remains');
    if (await page.locator('.roadmap-node-details[open], .roadmap-tools[open], .roadmap-history[open]').count()) throw new Error('Details opened by default');
    await page.locator(`#node-${first.id} .roadmap-node-details > summary`).click();
    await page.locator(`#node-${first.id}`).getByText(`当前待办：${first.todo.title} · 2026-10-09 · 待完成`, { exact: true }).waitFor();
    await page.locator(`#node-${first.id}`).getByText('历史计划日期（只读）', { exact: true }).click();
    if (!(await page.locator(`#node-${first.id}`).innerText()).includes('2026-10-01')) throw new Error('Historical plan not preserved');
    await page.locator(`#node-${first.id} .roadmap-node-details > summary`).click();
    await openRevision();
    const blocked = revision.getByRole('article', { name: '调整方案', exact: true }).filter({ hasText: '含日期变更，不能确认' });
    if (await blocked.count() !== 1 || await blocked.getByRole('button').count() || await blocked.getByRole('checkbox').count()) throw new Error('Legacy date-changing revision executable');
    await revision.getByText('手动编辑调整草稿（无需模型）', { exact: true }).click();
    if (await revision.locator('input[type="date"]').count()) throw new Error('Revision still edits dates');
    await noOverflow();
    await page.locator('.roadmap-tools > summary').click();
    await page.getByText('历史排期记录（只读）', { exact: true }).click();
    const history = page.getByRole('region', { name: '历史排期记录', exact: true });
    if (await history.getByRole('button').count() || await history.locator('input').count()) throw new Error('Legacy schedule history executable');
    await history.getByRole('heading', { name: '历史未应用方案（不能确认）', exact: true }).waitFor();
    await page.getByText('历史排期记录（只读）', { exact: true }).click();
    await page.locator(`#node-${second.id}`).getByRole('checkbox').check();
    await page.locator(`#node-${third.id}`).getByRole('checkbox').check();
    const confirmation = page.getByRole('region', { name: '确认加入清单', exact: true });
    if ((await confirmation.getByRole('listitem').allTextContents()).join('|') !== [second.todo_title, third.todo_title].join('|')) throw new Error('Confirmation titles/order differ from selection');
    const text = await confirmation.innerText();
    if (!text.includes('本次待新增 2 项') || !text.includes('不安排日期') || /2026-10-0[23]/.test(text)) throw new Error('Join confirmation inherits historical dates');
    if ((await todos()).length !== 1 || business(await read()) !== business(initial)) throw new Error('Read or selection mutated business state');
    await noOverflow();
    await page.screenshot({ path: `output/playwright/issue35/self-paced-${width}.png`, fullPage: true });
  }
  await perform(() => page.getByRole('button', { name: '确认加入所选 2 项', exact: true }).click());
  const joined = await read();
  if ((await todos()).length !== 3 || joined.nodes.slice(1).some(node => node.todo.scheduled_date !== null)) throw new Error('New joined todos inherited dates');
  if (joined.nodes[0].todo.scheduled_date !== '2026-10-09') throw new Error('Existing todo date was cleared');
  await openRoute();
  await openRevision();
  await revision.getByText('手动编辑调整草稿（无需模型）', { exact: true }).click();
  await revision.getByRole('group', { name: '草稿节点 2', exact: true }).getByLabel('练习', { exact: true }).fill('设置缓存过期时间，然后记录过期前后的读取结果');
  const preview = await perform(() => revision.getByRole('button', { name: '预览手动调整', exact: true }).click());
  if (preview.payload.action.arguments.nodes.some(node => Object.hasOwn(node, 'scheduled_date'))) throw new Error('Content-only draft submitted date fields');
  if (business(await read()) !== business(joined)) throw new Error('Content preview mutated route');
  await openRoute();
  await openRevision();
  const proposal = revision.getByRole('article', { name: '调整方案', exact: true }).filter({ has: page.getByRole('heading', { name: '待确认调整', exact: true }) });
  await proposal.getByRole('checkbox', { name: `同意同步待办及关联学习内容：${second.todo_title}`, exact: true }).check();
  await perform(() => proposal.getByRole('button', { name: '确认此调整方案', exact: true }).click());
  const revised = await read();
  if (revised.nodes[1].exercise !== '设置缓存过期时间，然后记录过期前后的读取结果') throw new Error('Content revision not applied');
  for (let index = 0; index < joined.nodes.length; index++) {
    if (revised.nodes[index].planned_date !== joined.nodes[index].planned_date || revised.nodes[index].todo.scheduled_date !== joined.nodes[index].todo.scheduled_date) throw new Error('Content revision changed A or B');
  }
  await nav.getByRole('link', { name: /^待办/ }).click();
  await page.getByRole('button', { name: `编辑待办：${first.todo_title}`, exact: true }).click();
  const editor = page.getByRole('form', { name: `编辑待办：${first.todo_title}`, exact: true });
  await editor.getByLabel('安排日期', { exact: true }).fill('2026-10-12');
  await perform(() => editor.getByRole('button', { name: '保存修改', exact: true }).click());
  await perform(() => page.getByRole('button', { name: `标记完成：${second.todo_title}`, exact: true }).click());
  await openRoute();
  await route.getByText('学习进度：已完成 1 / 3， 剩余 2 个节点', { exact: true }).waitFor();
  let current = await read();
  if (current.nodes[0].todo.scheduled_date !== '2026-10-12' || current.nodes[0].planned_date !== '2026-10-01') throw new Error('Ordinary manual date not preserved separately');
  if (!current.nodes[1].completion || current.nodes[1].status !== 'completed') throw new Error('Todo completion did not sync');
  const completion = current.nodes[1].completion;
  await page.locator(`#node-${first.id} .roadmap-node-details > summary`).click();
  await page.locator(`#node-${first.id}`).getByText(`当前待办：${first.todo_title} · 2026-10-12 · 待完成`, { exact: true }).waitFor();
  await page.screenshot({ path: 'output/playwright/issue35/self-paced-current-date.png', fullPage: true });
  await nav.getByRole('link', { name: '对话', exact: true }).click();
  await page.getByRole('button', { name: '＋ 新会话', exact: true }).click();
  await openRoute();
  current = await read();
  if (JSON.stringify(current.nodes[1].completion) !== JSON.stringify(completion) || (await todos()).length !== 3) throw new Error('Cross-session restore lost facts');
  const pendingPanel = page.getByRole('region', { name: '待续学习需求', exact: true });
  const sqlite = pendingPanel.getByRole('article').filter({ has: page.getByRole('heading', { name: 'SQLite', exact: true }) });
  if (await sqlite.getByRole('textbox').inputValue()) throw new Error('Continuation unexpectedly pre-filled');
  if (!await sqlite.getByRole('button', { name: '继续此学习需求', exact: true }).isEnabled()) throw new Error('Legacy time-only continuation requires text');
  const continued = await perform(() => sqlite.getByRole('button', { name: '继续此学习需求', exact: true }).click());
  const pendingAfter = await readJson('/api/learning-requests');
  const selected = initialPending.find(item => item.topic === 'SQLite');
  const untouched = initialPending.find(item => item.topic === 'Python');
  if (continued.payload.action.arguments.request_id !== selected.id || !pendingAfter.find(item => item.id === selected.id).roadmap_id || pendingAfter.find(item => item.id === untouched.id).roadmap_id) throw new Error('Continuation targeted a different pending request');
  if ((await todos()).length !== 3 || (await readJson('/api/roadmaps')).length !== 2) throw new Error('Continuation created todos or duplicate routes');
  await page.reload();
  if ((await readJson('/api/roadmaps')).length !== 2) throw new Error('Refresh auto-continued pending request');
  await noOverflow();
  return { widths: [1440, 390], roadmap_id: id, todos: 3, continued_request: selected.id,
    checks: 'read-only legacy history, no date inputs, exact selection, new dates empty, date A/B retention, content draft omission, manual todo date, completion, cross-session recovery, explicit blank continuation without cross-targeting',
    submitted_tools: submitted.map(item => item.action.tool), providers: 'simulated' };
}
