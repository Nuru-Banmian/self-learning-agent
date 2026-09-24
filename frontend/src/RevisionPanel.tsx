import { useState } from "react";
import type { Node, Roadmap, RoadmapAction } from "./RoadmapPanel";

type ProposedNode = Pick<Node, "goal" | "estimated_minutes" | "source_ids" | "exercise" | "completion_criteria" | "todo_title"> & { node_id: string | null };
type ChangeNode = Omit<ProposedNode, "node_id"> & { id: string; position: number; scheduled_date: string | null };
export type RevisionProposal = {
  id: string;
  status: "pending" | "applied" | "stale";
  date_changes_blocked: boolean;
  before: { title: string; goal: string };
  after: { title: string; goal: string };
  sync_todo_ids: string[];
  gaps: string[];
  sources: Roadmap["sources"];
  entries: {
    kind: "add" | "modify" | "archive";
    before: ChangeNode | null;
    after: ChangeNode | null;
    todo_before: (NonNullable<Node["todo"]> & { id: string }) | null;
    todo_after: Node["todo"];
  }[];
};

function NodeChange({ label, node }: { label: string; node: ChangeNode | null }) {
  return node ? <div><strong>{label}：第 {node.position} 步 · {node.goal}</strong>
    <p>预计 {node.estimated_minutes} 分钟{node.scheduled_date && ` · 方案记录日期：${node.scheduled_date}`}</p>
    <p>练习：{node.exercise}</p><p>完成标准：{node.completion_criteria}</p>
    <p>候选待办：{node.todo_title} · 资料：{node.source_ids.join("、")}</p>
  </div> : <p>{label}：无</p>;
}

function Proposal({ proposal: p, route, disabled, act }: {
  proposal: RevisionProposal; route: Roadmap; disabled: boolean;
  act: (content: string, action: RoadmapAction) => void;
}) {
  const [sync, setSync] = useState<string[]>([]);
  const confirm = (ids: string[]) => act(
    `${ids.length === p.sync_todo_ids.length ? "确认调整" : "暂不同步"}：${route.title}，方案 ${p.id}`,
    { tool: "confirm_roadmap_revision", arguments: { roadmap_id: route.id, proposal_id: p.id, sync_todo_ids: ids } },
  );
  return <article aria-label="调整方案" className="roadmap-confirmation">
    <h4>{p.date_changes_blocked ? "历史调整方案（含日期变更，不能确认）" : p.status === "pending" ? "待确认调整" : p.status === "applied" ? "已应用调整（历史方案）" : "调整方案冲突，请重新生成"}</h4>
    {p.date_changes_blocked && <p role="status">此方案包含已停用的日期变更，请重新生成只调整学习内容的方案。</p>}
    <p>标题：{p.before.title} → {p.after.title}</p><p>目标：{p.before.goal} → {p.after.goal}</p>
    {p.entries.map((e, i) => <section key={i}>
      <h5>{e.kind === "add" ? "新增候选（不自动加入待办）" : e.kind === "archive" ? "移入历史，保留待办与完成记录" : "修改节点"}</h5>
      <NodeChange label="当前" node={e.before} /><NodeChange label="调整后" node={e.after} />
      {e.todo_before && <div>
        <p>关联待办 {e.todo_before.id}：{e.todo_before.title} · {e.todo_before.scheduled_date || "未安排"} → {e.todo_after?.title} · {e.todo_after?.scheduled_date || "未安排"}</p>
        <p>待办状态：{e.todo_before.status === "completed" ? "已完成，保留完成事实" : "待完成"}</p>
        {p.status === "pending" && !p.date_changes_blocked && p.sync_todo_ids.includes(e.todo_before.id) && <label>
          <input type="checkbox" disabled={disabled} checked={sync.includes(e.todo_before.id)} onChange={event => {
            const id = e.todo_before!.id;
            setSync(previous => event.target.checked ? [...previous, id] : previous.filter(v => v !== id));
          }} />同意同步待办及关联学习内容：{e.todo_before.title}
        </label>}
      </div>}
    </section>)}
    <details><summary>方案资料来源</summary>{p.sources.map(s => <p key={s.id}>[{s.id}] <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> · {s.material_type === "body" ? "已取得正文" : "仅搜索摘要"}<br />{s.snippet}</p>)}</details>
    {p.gaps.map((gap, i) => <p className="error" key={i}>{gap}</p>)}
    {p.status === "pending" && !p.date_changes_blocked && <div className="roadmap-actions">
      <button disabled={disabled || !p.sync_todo_ids.every(id => sync.includes(id))} onClick={() => confirm(sync)}>确认此调整方案</button>
      {p.sync_todo_ids.length > 0 && <button disabled={disabled} onClick={() => confirm([])}>暂不同步，保留整份方案</button>}
    </div>}
  </article>;
}

export function RevisionPanel({ route, disabled, act }: {
  route: Roadmap; disabled: boolean; act: (content: string, action: RoadmapAction) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [unjoined, setUnjoined] = useState(false);
  const [title, setTitle] = useState(route.title);
  const [goal, setGoal] = useState(route.goal);
  const [nodes, setNodes] = useState<ProposedNode[]>(() => route.nodes.map(n => ({
    node_id: n.id, goal: n.goal, estimated_minutes: n.estimated_minutes, source_ids: n.source_ids,
    exercise: n.exercise, completion_criteria: n.completion_criteria, todo_title: n.todo_title,
  })));
  const patch = (index: number, value: Partial<ProposedNode>) => setNodes(previous => previous.map((n, i) => i === index ? { ...n, ...value } : n));
  return <section aria-label="路线调整" className="roadmap-confirmation">
    <h4>调整学习路线</h4><p>先生成并审阅内容差异，确认后应用。已有日期保持原样，拒绝同步时整份方案保持待确认。</p>
    <label>调整要求<textarea aria-label="调整要求" value={instruction} maxLength={2000} onChange={e => setInstruction(e.target.value)} placeholder="例如：太难了，改简单一点" /></label>
    <label><input type="checkbox" checked={unjoined} onChange={e => setUnjoined(e.target.checked)} />只调整未加入节点（重新生成方案）</label>
    <button disabled={disabled || !instruction.trim()} onClick={() => act(`请求调整路线「${route.title}」：${instruction}`, {
      tool: "revise_roadmap", arguments: { roadmap_id: route.id, expected_version: route.version, instruction, unjoined_only: unjoined },
    })}>生成调整方案</button>
    <details><summary>手动编辑调整草稿（无需模型）</summary>
      <label>路线标题<input value={title} maxLength={200} onChange={e => setTitle(e.target.value)} /></label>
      <label>路线目标<input value={goal} maxLength={400} onChange={e => setGoal(e.target.value)} /></label>
      {nodes.map((node, index) => {
        const completed = route.nodes.find(n => n.id === node.node_id)?.status === "completed";
        return <fieldset key={node.node_id || `new-${index}`}><legend>草稿节点 {index + 1}{completed ? "（已完成，内容受保护）" : ""}</legend>
          <fieldset disabled={disabled || completed}>
            <label>学习目标<input value={node.goal} maxLength={300} onChange={e => patch(index, { goal: e.target.value })} /></label>
            <label>预计分钟<input type="number" min={1} max={480} value={node.estimated_minutes} onChange={e => patch(index, { estimated_minutes: Number(e.target.value) })} /></label>
            <label>练习<textarea aria-label="练习" value={node.exercise} maxLength={2400} onChange={e => patch(index, { exercise: e.target.value })} /></label>
            <label>完成标准<textarea aria-label="完成标准" value={node.completion_criteria} maxLength={400} onChange={e => patch(index, { completion_criteria: e.target.value })} /></label>
            <label>候选待办标题<input value={node.todo_title} maxLength={200} onChange={e => patch(index, { todo_title: e.target.value })} /></label>
            <label>资料（可多选）<select multiple value={node.source_ids} onChange={e => patch(index, { source_ids: Array.from(e.target.selectedOptions, o => o.value) })}>{route.sources.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
          </fieldset>
          <button disabled={disabled || index === 0} onClick={() => setNodes(previous => { const next = [...previous]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next; })}>上移节点</button>
          <button disabled={disabled || nodes.length <= 1} onClick={() => setNodes(previous => previous.filter((_, i) => i !== index))}>移出当前顺序（保留历史）</button>
        </fieldset>;
      })}
      <button disabled={disabled || nodes.length >= 8} onClick={() => setNodes(previous => [...previous, {
        node_id: null, goal: "", estimated_minutes: 30, source_ids: route.sources.slice(0, 1).map(s => s.id), exercise: "", completion_criteria: "", todo_title: "",
      }])}>新增候选节点</button>
      <button disabled={disabled || !title.trim() || !goal.trim() || nodes.some(n => !n.goal.trim() || !n.exercise.trim() || !n.completion_criteria.trim() || !n.todo_title.trim() || !n.source_ids.length || !Number.isInteger(n.estimated_minutes) || n.estimated_minutes < 1 || n.estimated_minutes > 480)} onClick={() => act(`预览路线「${route.title}」的手动调整`, {
        tool: "preview_roadmap_revision", arguments: { roadmap_id: route.id, expected_version: route.version, title, goal, nodes },
      })}>预览手动调整</button>
    </details>
    {route.revision_proposals.filter(p => p.status === "pending").map(p => <Proposal key={p.id} proposal={p} route={route} disabled={disabled} act={act} />)}
    {route.revision_proposals.some(p => p.status !== "pending") && <details>
      <summary>历史调整方案</summary>
      {route.revision_proposals.filter(p => p.status !== "pending").map(p => <Proposal key={p.id} proposal={p} route={route} disabled={disabled} act={act} />)}
    </details>}
  </section>;
}
