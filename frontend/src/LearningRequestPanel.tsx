import { useState } from "react";

export type LearningRequest = {
  id: string;
  topic: string;
  roadmap_id: string | null;
  known: Record<string, string | null>;
  questions: string[];
  messages: { id: string; content: string }[];
  memories: { id: string; content: string }[];
};
type ContinueAction = {
  tool: "continue_learning";
  arguments: { request_id: string };
};

export function LearningRequestPanel({ requests, disabled, act }: {
  requests: LearningRequest[];
  disabled: boolean;
  act: (content: string, action: ContinueAction) => void;
}) {
  const [replies, setReplies] = useState<Record<string, string>>({});
  const pending = requests.filter((r) => !r.roadmap_id);
  return <section className="suggestions" aria-label="待续学习需求">
    <h2>待续学习需求</h2>
    <p>补充缺少的信息即可继续；刷新或新会话后仍可找回。</p>
    {!pending.length && <p>暂无待续需求。</p>}
    {pending.map((request) => <article className="suggestion" key={request.id}>
      <h3>{request.topic}</h3>
      <p>上次已知：{Object.values(request.known).filter(Boolean).join("；") || "待补充"}</p>
      {request.questions.map((question) => <p key={question}>{question}</p>)}
      {!request.questions.length && <p>信息已补齐，可继续生成路线。</p>}
      <details><summary>上次采用的记忆与用户原话</summary>
        <p>续答时会重新核对生效记忆；以下为上次记录。</p>
        {request.memories.map((m) => <p key={m.id}>{m.content}</p>)}
        {request.messages.map((m) => <p key={m.id}>{m.content}</p>)}
      </details>
      <label>补充 {request.topic}
        <textarea value={replies[request.id] || ""} maxLength={8000}
          onChange={(event) => setReplies({ ...replies, [request.id]: event.target.value })} />
      </label>
      <button disabled={disabled || !replies[request.id]?.trim()} onClick={() => {
        act(replies[request.id].trim(), { tool: "continue_learning", arguments: { request_id: request.id } });
        setReplies({ ...replies, [request.id]: "" });
      }}>继续此学习需求</button>
    </article>)}
  </section>;
}
