import { useEffect, useState } from "react";

export type RoadmapSummary = {
  id: string;
  title: string;
  goal: string;
  version: number;
  status: string;
};
type Node = {
  id: string;
  position: number;
  goal: string;
  estimated_minutes: number;
  exercise: string;
  completion_criteria: string;
  todo_title: string;
  source_ids: string[];
  todo_id: string | null;
  todo: { title: string; scheduled_date: string | null; status: string } | null;
};
type Roadmap = RoadmapSummary & {
  request: string;
  nodes: Node[];
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
  tool: "accept_roadmap_node";
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
  useEffect(() => {
    const changed = () => setSelection(hashSelection());
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    let cancelled = false;
    setRoute(null);
    setError("");
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
  return (
    <section
      className="suggestions roadmaps"
      aria-label="学习路线"
      id="learning-roadmaps"
    >
      <h2>学习路线</h2>
      <p className="list-note">
        跨会话保存 · 每个节点可单独加入待办 · 默认未安排日期
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
          <details>
            <summary>原始学习需求</summary>
            <p>{route.request}</p>
          </details>
          {route.gaps.map((gap, i) => (
            <p className="error" key={i}>
              {gap}
            </p>
          ))}
          <p>是否将节点加入待办？预计耗时仅供参考。</p>
          <a href="#learning-roadmaps" className="roadmap-link">
            暂不加入（保留路线）
          </a>
          {route.nodes.map((node) => (
            <section
              key={node.id}
              id={`node-${node.id}`}
              className="suggestion"
              aria-label={`节点 ${node.position}`}
            >
              <h4>
                {node.position}. {node.goal}
              </h4>
              <p>预计 {node.estimated_minutes} 分钟</p>
              <p>练习：{node.exercise}</p>
              <p>完成标准：{node.completion_criteria}</p>
              <p>候选待办：{node.todo_title}</p>
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
              <button
                className="quiet"
                disabled={disabled || !!node.todo_id}
                onClick={() =>
                  act(`把节点 ${node.id} 加入待办`, {
                    tool: "accept_roadmap_node",
                    arguments: {
                      roadmap_id: route.id,
                      node_id: node.id,
                      expected_version: route.version,
                    },
                  })
                }
              >
                {node.todo_id ? "已加入待办" : "将此节点加入待办"}
              </button>
              <details>
                <summary>节点标识</summary>
                <small>{node.id}</small>
              </details>
            </section>
          ))}
        </article>
      )}
    </section>
  );
}
