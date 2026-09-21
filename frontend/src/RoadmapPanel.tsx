import { RevisionPanel, type RevisionProposal } from "./RevisionPanel";
import { useEffect, useState } from "react";
import { SchedulePanel, type ScheduleProposal } from "./SchedulePanel";

export type RoadmapSummary = {
  id: string;
  title: string;
  goal: string;
  version: number;
  status: string;
};
export type Node = {
  id: string;
  position: number;
  goal: string;
  estimated_minutes: number;
  exercise: string;
  completion_criteria: string;
  todo_title: string;
  source_ids: string[];
  todo_id: string | null;
  scheduled_date: string | null;
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
  tool: "revise_roadmap" | "preview_roadmap_revision" | "confirm_roadmap_revision" | "accept_roadmap_nodes" | "complete_roadmap_node" | "preview_roadmap_schedule" | "confirm_roadmap_schedule";
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
    if (route && selection.node)
      document
        .getElementById(`node-${selection.node}`)
        ?.scrollIntoView({ block: "center" });
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
        跨会话保存 · 可全选或选择部分节点加入待办 · 默认未安排日期
      </p>
      {!roadmaps.length && (
        <p>说出想学的主题、基础与目标，生成有资料来源的路线。</p>
      )}
      <nav aria-label="路线列表">
        {roadmaps.map((r) => (
          <a key={r.id} href={`#roadmap-${r.id}`} className="roadmap-link">
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
          <h3>{route.title}</h3>
          <p>目标：{route.goal}</p>
          <p role="status">
            学习进度：已完成 {route.progress.completed} / {route.progress.total}，
            剩余 {route.progress.remaining} 个节点
          </p>
          <details>
            <summary>原始学习需求</summary>
            <p>{route.request}</p>
          </details>
          {route.gaps.map((gap, i) => (
            <p className="error" key={i}>
              {gap}
            </p>
          ))}
          <RevisionPanel key={`${route.id}-${route.version}`} route={route} disabled={disabled} act={act} />
          <SchedulePanel key={route.id} route={route} disabled={disabled} act={act} />
          <p>是否将节点加入待办？预计耗时仅供参考。</p>
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
          <section
            className="roadmap-confirmation"
            aria-label="确认加入清单"
            aria-live="polite"
          >
            <p>本次待新增 {pending.length} 项 · 沿用已确认的节点日期</p>
            {pending.length ? (
              <ul>
                {pending.map((node) => (
                  <li key={node.id}>{node.todo_title} · {node.scheduled_date || "未安排"}</li>
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
          {[...route.nodes, ...route.history_nodes].map((node) => (
            <section
              key={node.id}
              id={`node-${node.id}`}
              className="suggestion"
              aria-label={node.position < 0 ? `历史节点 ${node.goal}` : `节点 ${node.position}`}
            >
              <h4>
                {node.position < 0 ? "历史节点（保留关联与记录）" : `${node.position}.`} {node.goal}
              </h4>
              <p>预计 {node.estimated_minutes} 分钟</p>
              <p>当前节点安排：{node.scheduled_date || "未安排"}</p>
              <p>练习：{node.exercise}</p>
              <p>完成标准：{node.completion_criteria}</p>
              <p>候选待办：{node.todo_title}</p>
              <p>节点状态：{node.status === "completed" ? "已完成" : "待完成"}</p>
              {node.completion && (
                <details>
                  <summary>完成记录 · {new Date(node.completion.completed_at).toLocaleString()}</summary>
                  <p>{node.completion.operation === "mastered" ? "用户标记已掌握" : "关联待办完成"}</p>
                  <p>操作依据：{node.completion.content}</p>
                  <p>完成时目标：{node.completion.node.goal}</p>
                  <p>完成时练习：{node.completion.node.exercise}</p>
                  <p>完成时标准：{node.completion.node.completion_criteria}</p>
                </details>
              )}
              {node.todo && (
                <p>
                  当前待办：{node.todo.title} ·{" "}
                  {node.todo.scheduled_date || "未安排"} ·{" "}
                  {node.todo.status === "completed" ? "已完成" : "待完成"}
                </p>
              )}
              {node.source_ids.map((id) => {
                const source = route.sources.find((s) => s.id === id);
                return (
                  source && (
                    <details key={id}>
                      <summary>
                        资料 [{id}] {source.title} ·{" "}
                        {source.material_type === "body"
                          ? "已取得正文"
                          : "仅搜索摘要"}
                      </summary>
                      <a href={source.url} target="_blank" rel="noreferrer">
                        {source.title}
                      </a>
                      <p>搜索摘要：{source.snippet}</p>
                      {source.body ? (
                        <p className="source-body">
                          正文{source.body_truncated ? "（截取片段）" : ""}：
                          {source.body}
                        </p>
                      ) : (
                        <p>未读取正文</p>
                      )}
                    </details>
                  )
                );
              })}
              <label>
                <input
                  type="checkbox"
                  disabled={
                    disabled || node.position < 0 || !!node.todo_id || node.status === "completed"
                  }
                  checked={checked.includes(node.id)}
                  onChange={(event) =>
                    setChecked((previous) =>
                      event.target.checked
                        ? [...previous, node.id]
                        : previous.filter((id) => id !== node.id),
                    )
                  }
                />
                {node.todo_id
                  ? "已加入待办"
                  : node.status === "completed"
                    ? "已完成，跳过"
                    : `选择节点 ${node.position}：${node.todo_title}`}
              </label>
              <details>
                <summary>节点标识</summary>
                <small>{node.id}</small>
              </details>
              <button
                disabled={disabled || node.position < 0 || node.status === "completed"}
                onClick={() => act(`将节点「${node.goal}」标记为已掌握`, {
                  tool: "complete_roadmap_node",
                  arguments: {
                    roadmap_id: route.id,
                    node_id: node.id,
                    expected_version: route.version,
                  },
                })}
              >
                标记已掌握{node.todo_id ? "（同步完成待办）" : "（不创建待办）"}
              </button>
            </section>
          ))}
        </article>
      )}
    </section>
  );
}
