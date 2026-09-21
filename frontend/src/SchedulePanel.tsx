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

export function SchedulePanel({ proposals }: {
  proposals: ScheduleProposal[];
}) {
  return <section className="roadmap-confirmation" aria-label="历史排期记录">
    <p>路线排期已取消。以下方案仅供查阅，不能确认，也不用于新加入待办。当前日期以关联待办为准。</p>
    {proposals.map(p => <article key={p.id} aria-label="历史排期方案">
      <h4>{p.status === "pending" ? "历史未应用方案（不能确认）" : p.status === "applied" ? "历史已应用方案" : "历史已过期方案"}</h4>
      {p.basis.clear ? <p>方案内容：清空全部未完成节点日期。</p> : <p>
        方案内容：从 {p.basis.start_date} 起，每个学习日 {p.basis.daily_minutes} 分钟，
        周{p.basis.weekdays?.map(d => "一二三四五六日"[d]).join("、")}；
        期限 {p.basis.deadline || "未指定"}；时区 {p.basis.timezone}。
        日期为预计完成日，耗时较长的节点分多天学习。
      </p>}
      <ul>{p.entries.map(e => <li key={e.node_id}>
        {e.position}. {e.title}：{e.previous_date || "未安排"} → {e.scheduled_date || "未安排"}
        <p>{e.todo_id ? "当时已关联待办" : "当时未关联待办"}</p>
        {e.allocations.length > 0 && <p>{e.allocations.map(a => `${a.date} 学习 ${a.minutes} 分钟`).join("；")}</p>}
      </li>)}</ul>
    </article>)}
  </section>;
}
