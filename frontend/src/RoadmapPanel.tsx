import { RevisionPanel, type RevisionProposal } from "./RevisionPanel";
import { useEffect, useState } from "react";
import { SchedulePanel, type ScheduleProposal } from "./SchedulePanel";
import { RoadmapNode } from "./RoadmapNode";
import "./roadmap-panel.css";

export type RoadmapSummary = {
  id: string;
  title: string;
  goal: string;
  version: number;
  status: string;
  display_title?: string;
};
export type Node = {
  id: string;
  position: number;
  goal: string;
  display_title?: string;
  display_goal?: string;
  estimated_minutes: number;
  exercise: string;
  completion_criteria: string;
  todo_title: string;
  source_ids: string[];
  todo_id: string | null;
  scheduled_date: string | null;
  planned_date: string | null;
  status?: string;
  completion: {
    completed_at: string;
    operation: string;
    content: string;
    node: { goal: string; exercise: string; completion_criteria: string };
  } | null;
  todo: { title: string; scheduled_date: string | null; status: string } | null;
};
export type Roadmap = RoadmapSummary & {
  request: string;
  schedule_proposals: ScheduleProposal[];
  nodes: Node[];
  history_nodes: Node[];
  revision_proposals: RevisionProposal[];
  progress: { completed: number; total: number; remaining: number };
  gaps: string[];
  sources: {
    id: string;
    title: string;
    url: string;
    snippet: string;
    material_type: string;
    body: string | null;
    body_truncated?: boolean;
  }[];
};
export type RoadmapAction = {
  tool: "revise_roadmap" | "preview_roadmap_revision" | "confirm_roadmap_revision" | "accept_roadmap_nodes" | "complete_roadmap_node";
  arguments: Record<string, unknown>;
};

function hashSelection() {
  const match = /^#roadmap-([\w-]+)(?:\/([\w-]+))?$/.exec(window.location.hash);
  return match ? { id: match[1], node: match[2] || "" } : { id: "", node: "" };
}

export function RoadmapPanel({
  roadmaps,
  disabled,
  act,
}: {
  roadmaps: RoadmapSummary[];
  disabled: boolean;
  act: (content: string, action: RoadmapAction) => void;
}) {
  const [selection, setSelection] = useState(hashSelection);
  const [route, setRoute] = useState<Roadmap | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [checked, setChecked] = useState<string[]>([]);
  useEffect(() => {
    const changed = () => setSelection(hashSelection());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    let cancelled = false;
    setRoute(previous => previous?.id === selection.id ? previous : null);
    setError("");
    setChecked([]);
    if (selection.id) {
      fetch(`/api/roadmaps/${selection.id}`)
        .then(async (response) => {
          if (!response.ok) throw new Error("路线读取失败，请重试。");
          return response.json() as Promise<Roadmap>;
        })
        .then((data) => {
          if (!cancelled) setRoute(data);
        })
        .catch(() => {
          if (!cancelled) setError("路线读取失败，请重试。");
        });
    }
    return () => {
      cancelled = true;
    };
  }, [selection.id, roadmaps, reload]);
  useEffect(() => {
    if (route && selection.node) {
      const node = document.getElementById(`node-${selection.node}`);
      const history = node?.closest<HTMLDetailsElement>(".roadmap-history");
      if (history) history.open = true;
      node?.scrollIntoView({ block: "center" });
      node?.focus({ preventScroll: true });
    }
  }, [route, selection.node]);
  const eligible =
    route?.nodes.filter(
      (node) => !node.todo_id && node.status !== "completed",
    ) || [];
  const pending = eligible.filter((node) => checked.includes(node.id));
  return (
    <section
      className="suggestions roadmaps"
      aria-label="学习路线"
      id="learning-roadmaps"
    >
      <h2>学习路线</h2>
      <p className="list-note">
        跨会话保存 · 可全选或选择部分节点加入待办 · 按自己的节奏完成
      </p>
      {!roadmaps.length && (
        <p>说出想学的主题、基础与目标，生成有资料来源的路线。</p>
      )}
      <nav aria-label="路线列表">
        {roadmaps.map((r) => (
          <a key={r.id} href={`#roadmap-${r.id}`} className="roadmap-link" aria-current={selection.id === r.id ? "location" : undefined}>
            查看路线：{r.title}（
            {r.status === "partial" ? "部分结果" : "资料已取得"}）
          </a>
        ))}
      </nav>
      {error && (
        <p role="alert">
          {error}{" "}
          <button onClick={() => setReload(reload + 1)}>重试读取</button>
        </p>
      )}
      {selection.id && !route && !error && <p>正在读取路线…</p>}
      {route && (
        <article aria-label="路线详情">
          <h3>{route.display_title || route.title}</h3>
          <p>目标：{route.goal}</p>
          <p role="status">
            学习进度：已完成 {route.progress.completed} / {route.progress.total}，
            剩余 {route.progress.remaining} 个节点
          </p>
          <details>
            <summary>原始学习需求</summary>
            <p>{route.request}</p>
          </details>
          {(route.status === "partial" || route.gaps.length > 0) && (
            <div className="roadmap-limitations" role="status">
              <p>资料部分取得，可先查看已有路线；缺失内容仍待补充。</p>
              <details>
                <summary>查看资料缺口</summary>
                {route.gaps.map((gap, i) => <p key={i}>{gap}</p>)}
              </details>
            </div>
          )}
          <p>勾选想做的节点，确认后加入待办。</p>
          <a href="#learning-roadmaps" className="roadmap-link">
            暂不加入（保留路线）
          </a>
          <div className="roadmap-actions">
            <button
              disabled={disabled || !eligible.length}
              onClick={() => setChecked(eligible.map((node) => node.id))}
            >
              全部选择未加入节点
            </button>
            <button
              disabled={disabled || !checked.length}
              onClick={() => setChecked([])}
            >
              清空选择
            </button>
          </div>
          <ol className="roadmap-nodes" aria-label="当前路线节点">
            {route.nodes.map((node) => (
              <li key={node.id}>
                <RoadmapNode node={node} route={route} disabled={disabled} checked={checked.includes(node.id)}
                  onCheck={(value) => setChecked(previous => value ? [...previous, node.id] : previous.filter(id => id !== node.id))}
                  act={act} />
              </li>
            ))}
          </ol>
          <section
            className="roadmap-confirmation"
            aria-label="确认加入清单"
            aria-live="polite"
          >
            <p>本次待新增 {pending.length} 项 · 加入后不安排日期，按自己的节奏完成</p>
            {pending.length ? (
              <ul>
                {pending.map((node) => (
                  <li key={node.id}>{node.todo_title}</li>
                ))}
              </ul>
            ) : (
              <p>请先选择节点；空选择不会新增待办。</p>
            )}
            <p>
              已加入或已完成的节点会跳过。其他页面先行加入时，以提交结果为准。
            </p>
            <button
              disabled={disabled || !pending.length}
              onClick={() =>
                act(
                  `确认加入路线「${route.title}」的 ${pending.length} 个所选节点`,
                  {
                    tool: "accept_roadmap_nodes",
                    arguments: {
                      roadmap_id: route.id,
                      node_ids: pending.map((node) => node.id),
                      expected_version: route.version,
                    },
                  },
                )
              }
            >
              确认加入所选 {pending.length} 项
            </button>
          </section>
          <details className="roadmap-tools" key={`${route.id}-${route.version}-revision`}>
            <summary>调整学习路线{route.revision_proposals.some(proposal => proposal.status === "pending" && !proposal.date_changes_blocked) ? " · 有待确认方案" : ""}</summary>
            <RevisionPanel route={route} disabled={disabled} act={act} />
          </details>
          {route.schedule_proposals.length > 0 && <details className="roadmap-history" key={`${route.id}-schedule`}>
            <summary>历史排期记录（只读）</summary>
            <SchedulePanel proposals={route.schedule_proposals} />
          </details>}
          {route.history_nodes.length > 0 && (
            <details className="roadmap-history" key={`${route.id}-history`}>
              <summary>历史节点与记录 · {route.history_nodes.length} 项</summary>
              {route.history_nodes.map(node => <RoadmapNode key={node.id} node={node} route={route} disabled={disabled} checked={false} onCheck={() => {}} act={act} />)}
            </details>
          )}
        </article>
      )}
    </section>
  );
}
