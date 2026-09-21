import { useState } from "react";
import type { RoadmapAction } from "./RoadmapPanel";

export type ScheduleProposal = {
  id: string;
  status: "pending" | "applied" | "stale";
  basis: {
    clear: boolean;
    start_date?: string;
    daily_minutes?: number;
    weekdays?: number[];
    deadline?: string | null;
    timezone: string;
  };
  entries: {
    node_id: string;
    position: number;
    title: string;
    todo_id: string | null;
    previous_date: string | null;
    scheduled_date: string | null;
    allocations: { date: string; minutes: number }[];
  }[];
};

export function SchedulePanel({ route, disabled, act }: {
  route: { id: string; title: string; version: number; schedule_proposals: ScheduleProposal[] };
  disabled: boolean;
  act: (content: string, action: RoadmapAction) => void;
}) {
  const [start, setStart] = useState("");
  const [minutes, setMinutes] = useState("");
  const [deadline, setDeadline] = useState("");
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const preview = (clear: boolean) => act(
    clear ? `预览清空路线「${route.title}」未完成节点日期` : `请求为路线「${route.title}」排期：从${start}起，每个学习日${minutes}分钟`,
    { tool: "preview_roadmap_schedule", arguments: {
      roadmap_id: route.id, expected_version: route.version,
      ...(clear ? { clear: true } : {
        start_text: start, daily_minutes: Number(minutes), weekdays: days,
        deadline_text: deadline || null,
      }),
    } },
  );
  return <section className="roadmap-confirmation" aria-label="路线排期">
    <h4>按需排期</h4>
    <p>为全部未完成节点安排学习日。先预览，确认后保存并同步已关联待办。</p>
    <label>起始日期<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
    <label>每个学习日可用分钟<input type="number" min="1" max="1440" value={minutes} onChange={e => setMinutes(e.target.value)} /></label>
    <fieldset><legend>可学习的星期</legend>
      {[0, 1, 2, 3, 4, 5, 6].map(d => <label key={d}>
        <input type="checkbox" checked={days.includes(d)} onChange={e => setDays(prev => e.target.checked ? [...prev, d] : prev.filter(v => v !== d))} />
        周{"一二三四五六日"[d]}
      </label>)}
    </fieldset>
    <label>截止日期（可选）<input type="date" value={deadline} onChange={e => setDeadline(e.target.value)} /></label>
    <div className="roadmap-actions">
      <button disabled={disabled || !start || !Number.isInteger(Number(minutes)) || Number(minutes) < 1 || Number(minutes) > 1440 || !days.length} onClick={() => preview(false)}>预览排期</button>
      <button disabled={disabled} onClick={() => preview(true)}>预览清空日期</button>
    </div>
    {route.schedule_proposals.map(p => <article key={p.id} aria-label="排期方案">
      <h4>{p.status === "pending" ? "待确认排期" : p.status === "applied" ? "已确认排期（历史方案）" : "方案已过期，请重新预览"}</h4>
      {p.basis.clear ? <p>清空全部未完成节点日期，恢复未安排。</p> : <p>
        从 {p.basis.start_date} 起，每个学习日 {p.basis.daily_minutes} 分钟，
        周{p.basis.weekdays?.map(d => "一二三四五六日"[d]).join("、")}；
        期限 {p.basis.deadline || "未指定"}；时区 {p.basis.timezone}。
        日期为预计完成日，耗时较长的节点分多天学习。
      </p>}
      <ul>{p.entries.map(e => <li key={e.node_id}>
        {e.position}. {e.title}：{e.previous_date || "未安排"} → {e.scheduled_date || "未安排"}
        <p>{e.todo_id ? "同步已关联待办" : "仅保存节点日期，不创建待办"}</p>
        {e.allocations.length > 0 && <p>{e.allocations.map(a => `${a.date} 学习 ${a.minutes} 分钟`).join("；")}</p>}
      </li>)}</ul>
      <p>已完成节点和完成记录保持原状。当前安排见下方节点。</p>
      {p.status === "pending" && <button disabled={disabled} onClick={() => act(`确认路线「${route.title}」的排期方案 ${p.id}`, {
        tool: "confirm_roadmap_schedule", arguments: { roadmap_id: route.id, proposal_id: p.id },
      })}>确认此排期方案</button>}
    </article>)}
  </section>;
}
