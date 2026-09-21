import type { Node, Roadmap, RoadmapAction } from "./RoadmapPanel";

export function RoadmapNode({ node, route, disabled, checked, onCheck, act }: {
  node: Node;
  route: Roadmap;
  disabled: boolean;
  checked: boolean;
  onCheck: (checked: boolean) => void;
  act: (content: string, action: RoadmapAction) => void;
}) {
  const historical = node.position < 0;
  const completed = node.status === "completed";
  return (
    <section id={`node-${node.id}`} className="suggestion roadmap-node" tabIndex={-1}
      aria-label={historical ? `历史节点 ${node.goal}` : `节点 ${node.position}`}>
      <h4>{historical ? "历史节点 · " : `${node.position}. `}{node.display_title || node.todo_title}</h4>
      <p className="roadmap-node-goal">{node.display_goal || node.goal}</p>
      <div className="roadmap-node-controls">
        <label>
          <input type="checkbox" disabled={disabled || historical || !!node.todo_id || completed}
            checked={checked} onChange={event => onCheck(event.target.checked)} />
          {completed ? "已完成" : node.todo_id ? "已加入待办" : `选择节点 ${node.position}`}
        </label>
        {!historical && !completed && (
          <button disabled={disabled} onClick={() => act(`将节点「${node.goal}」标记为已掌握`, {
            tool: "complete_roadmap_node",
            arguments: { roadmap_id: route.id, node_id: node.id, expected_version: route.version },
          })}>
            标记已掌握{node.todo_id ? "（同步完成待办）" : "（不创建待办）"}
          </button>
        )}
      </div>
      <details className="roadmap-node-details">
        <summary>查看节点详情、资料与练习</summary>
        <p>完整目标：{node.goal}</p>
        <p>练习：{node.exercise}</p>
        <p>完成标准：{node.completion_criteria}</p>
        <p>预计 {node.estimated_minutes} 分钟</p>
        <p>候选待办：{node.todo_title}</p>
        {node.todo && <p>当前待办：{node.todo.title} · {node.todo.scheduled_date || "未安排"} · {node.todo.status === "completed" ? "已完成" : "待完成"}</p>}
        {node.planned_date && <details>
          <summary>历史计划日期（只读）</summary>
          <p>{node.planned_date} · 仅保留历史记录，不用于新加入待办。</p>
        </details>}
        {node.source_ids.map(id => {
          const source = route.sources.find(item => item.id === id);
          return source ? (
            <details key={id} className="roadmap-source">
              <summary>资料：{source.title} · {source.material_type === "body" ? "已取得正文" : "仅搜索摘要"}{source.body_truncated ? "（截取片段）" : ""}</summary>
              <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
              <p>搜索摘要：{source.snippet}</p>
              {source.body ? <p className="source-body">正文{source.body_truncated ? "（截取片段）" : ""}：{source.body}</p> : <p>未读取正文</p>}
              <small>来源标识：{source.id}</small>
            </details>
          ) : <p key={id}>资料 {id} 暂不可读，请查看本路线的资料缺口。</p>;
        })}
        {node.completion && <details>
          <summary>完成记录 · {new Date(node.completion.completed_at).toLocaleString()}</summary>
          <p>{node.completion.operation === "mastered" ? "用户标记已掌握" : "关联待办完成"}</p>
          <p>操作依据：{node.completion.content}</p>
          <p>完成时目标：{node.completion.node.goal}</p>
          <p>完成时练习：{node.completion.node.exercise}</p>
          <p>完成时标准：{node.completion.node.completion_criteria}</p>
        </details>}
        <small>节点标识：{node.id}</small>
      </details>
    </section>
  );
}
