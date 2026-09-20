import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Message = { id: string; role: string; content: string };
type Todo = {
  id: string;
  title: string;
  scheduled_date: string | null;
  status: string;
  source: { content: string; message_id: string; session_id: string };
};
type Memory = {
  id: string;
  content: string;
  category: string;
  scope: string;
  topic: string;
  task_id: string | null;
  valid_from: string;
  expires_at: string | null;
  state: string;
  active: boolean;
  source: { content: string; message_id: string; session_id: string };
};
type MemoryEvidence = {
  learning?: string;
  saved_ids?: string[];
  loaded?: Memory[];
  usage?: { memory_id: string; reason: string }[];
};
type Run = {
  id: string;
  status: string;
  reply: string;
  todo_ids: string[];
  memory: MemoryEvidence;
};
const memoryCategories: Record<string, string> = {
  preference: "持续偏好",
  background: "背景",
  condition: "临时条件",
};
const memoryValidity: Record<string, string> = {
  ongoing: "持续有效",
  today: "仅今天",
  tomorrow: "仅明天",
  task: "直到对应待办完成",
};
type Action = {
  tool:
    | "update_todo"
    | "complete_todo"
    | "accept_suggestion"
    | "update_memory"
    | "delete_memory";
  arguments: Record<string, unknown>;
};
type Pending = {
  session: string;
  request_id: string;
  content: string;
  action?: Action;
};
type Health = { model_configured: boolean; timezone: string };
type Overview = { today: string; groups: Record<string, Todo[]> };
type Suggestion = {
  id: string;
  title: string;
  scheduled_date: string;
  todo_id: string | null;
};
const groups: Record<string, string> = {
  today: "今日未完成",
  overdue: "逾期未完成",
  unscheduled: "未安排",
  upcoming: "未来安排",
  completed: "已完成",
};

function MemoryEditor({
  memory,
  disabled,
  todos,
  act,
}: {
  memory: Memory;
  disabled: boolean;
  todos: Todo[];
  act: (content: string, action: Action) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [content, setContent] = useState(memory.content);
  const [topic, setTopic] = useState(memory.topic);
  const [scope, setScope] = useState(memory.scope);
  const [taskId, setTaskId] = useState(memory.task_id || "");
  const inferValidity = () =>
    memory.content.includes("今天")
      ? "today"
      : memory.content.includes("明天")
        ? "tomorrow"
        : memory.scope === "task"
          ? "task"
          : "ongoing";
  const [validity, setValidity] = useState(inferValidity);
  const target = {
    memory_id: memory.id,
    expected_source_id: memory.source.message_id,
  };
  return (
    <div className="memory-maintenance">
      <div className="todo-actions">
        <button
          className="quiet"
          disabled={disabled}
          onClick={() => {
            setContent(memory.content);
            setTopic(memory.topic);
            setScope(memory.scope);
            setTaskId(memory.task_id || "");
            setValidity(inferValidity());
            setEditing(!editing);
            setRemoving(false);
          }}
        >
          编辑记忆
        </button>
        <button
          className="quiet"
          disabled={disabled}
          onClick={() => {
            setRemoving(!removing);
            setEditing(false);
          }}
        >
          删除记忆
        </button>
      </div>
      {removing && (
        <div role="group" aria-label="确认删除记忆">
          <p>
            删除后不再用于回答。旧来源消息不会恢复此记忆；之后重新表达可以再次保存。
          </p>
          <button
            className="quiet"
            disabled={disabled}
            onClick={() =>
              act(`删除记忆：${memory.content}`, {
                tool: "delete_memory",
                arguments: target,
              })
            }
          >
            确认删除
          </button>
          <button className="quiet" onClick={() => setRemoving(false)}>
            取消
          </button>
        </div>
      )}
      {editing && (
        <form
          className="todo-editor"
          aria-label="编辑记忆"
          onSubmit={(event) => {
            event.preventDefault();
            act(
              `更正记忆：${memory.content} → ${content.trim()}（${scope === "task" ? "任务限定" : "一般范围"}，${memoryValidity[validity]}）`,
              {
                tool: "update_memory",
                arguments: {
                  ...target,
                  content: content.trim(),
                  topic: topic.trim(),
                  scope,
                  task_id: scope === "task" ? taskId : null,
                  validity,
                },
              },
            );
          }}
        >
          <label>
            记忆内容
            <input
              aria-label="记忆内容"
              required
              minLength={3}
              maxLength={300}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </label>
          <label>
            主题
            <input
              aria-label="记忆主题"
              required
              minLength={2}
              maxLength={30}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </label>
          <label>
            适用范围
            <select
              aria-label="适用范围"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="general">一般范围</option>
              <option value="task">指定待办</option>
            </select>
          </label>
          {scope === "task" && (
            <label>
              对应待办
              <select
                aria-label="对应待办"
                required
                value={taskId}
                onChange={(e) => setTaskId(e.target.value)}
              >
                <option value="">请选择待办</option>
                {todos
                  .filter((t) => t.status === "pending")
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <label>
            有效期限
            <select
              aria-label="有效期限"
              value={validity}
              onChange={(e) => setValidity(e.target.value)}
            >
              <option value="ongoing">持续有效</option>
              <option value="today">仅今天</option>
              <option value="tomorrow">仅明天</option>
              <option value="task">直到对应待办完成</option>
            </select>
          </label>
          <small>
            内容须是完整陈述，主题取自原文。临时条件请写明“今天”“明天”或完整待办标题，并选择对应范围、期限。
          </small>
          <button className="primary" disabled={disabled}>
            保存记忆修改
          </button>
          <button
            type="button"
            className="quiet"
            onClick={() => setEditing(false)}
          >
            取消编辑
          </button>
        </form>
      )}
    </div>
  );
}

function TodoCard({
  todo,
  disabled,
  act,
}: {
  todo: Todo;
  disabled: boolean;
  act: (content: string, action: Action) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(todo.title);
  const [date, setDate] = useState(todo.scheduled_date || "");
  return (
    <li>
      <div className="todo-title">
        <span className="checkbox">
          {todo.status === "completed" ? "✓" : ""}
        </span>
        <h3>{todo.title}</h3>
      </div>
      <div className="meta">
        <time>{todo.scheduled_date || "未安排"}</time>
        <span>{todo.status === "completed" ? "已完成" : "待完成"}</span>
      </div>
      <div className="todo-actions">
        <button
          className="quiet"
          disabled={disabled}
          onClick={() => {
            setTitle(todo.title);
            setDate(todo.scheduled_date || "");
            setEditing(!editing);
          }}
        >
          编辑
        </button>
        {todo.status !== "completed" && (
          <button
            className="quiet"
            disabled={disabled}
            onClick={() =>
              act(`完成待办：${todo.title}`, {
                tool: "complete_todo",
                arguments: { todo_id: todo.id },
              })
            }
          >
            标记完成
          </button>
        )}
      </div>
      {editing && (
        <form
          className="todo-editor"
          onSubmit={(event) => {
            event.preventDefault();
            const arguments_: Record<string, unknown> = { todo_id: todo.id };
            if (title.trim() !== todo.title) arguments_.title = title.trim();
            if (date !== (todo.scheduled_date || ""))
              arguments_.date_text = date || null;
            if (Object.keys(arguments_).length > 1)
              act(
                `修改待办：${todo.title} → ${title.trim()}，${date || "未安排"}`,
                { tool: "update_todo", arguments: arguments_ },
              );
            setEditing(false);
          }}
        >
          <label>
            标题
            <input
              aria-label="待办标题"
              required
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            安排日期
            <input
              aria-label="安排日期"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </label>
          <small>清空日期后归入未安排</small>
          <button className="primary" disabled={disabled || !title.trim()}>
            保存修改
          </button>
        </form>
      )}
      <details>
        <summary>查看来源与标识</summary>
        <p>{todo.source.content}</p>
        <small>
          待办 {todo.id}
          <br />
          消息 {todo.source.message_id}
        </small>
      </details>
    </li>
  );
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试。",
    );
  }
  return response.json() as Promise<T>;
}

function App() {
  const [session, setSession] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [todos, setTodos] = useState<Todo[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("准备就绪");
  const [error, setError] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [evidence, setEvidence] = useState<MemoryEvidence | null>(null);
  const stream = useRef<EventSource | null>(null);

  async function refresh(id: string) {
    const [data, summary, ideas, savedMemories] = await Promise.all([
      api<{ messages: Message[]; latest_run_id: string | null }>(
        `/sessions/${id}`,
      ),
      api<Overview>("/todos/overview"),
      api<Suggestion[]>(`/sessions/${id}/suggestions`),
      api<Memory[]>("/memories"),
    ]);
    setMessages(data.messages);
    setTodos(Object.values(summary.groups).flat());
    setOverview(summary);
    setSuggestions(ideas);
    setMemories(savedMemories);
    if (data.latest_run_id) {
      const latest = await api<Run>(`/runs/${data.latest_run_id}`);
      setEvidence(latest.memory);
    } else setEvidence(null);
  }

  async function newSession() {
    const created = await api<{ id: string }>("/sessions", {});
    localStorage.setItem("assistant-session", created.id);
    setSession(created.id);
    setMessages([]);
    setSuggestions([]);
    setPhase("准备就绪");
    setSavedCount(null);
    setEvidence(null);
    setMemories(await api<Memory[]>("/memories"));
    return created.id;
  }

  async function finish(runId: string, sessionId: string) {
    stream.current?.close();
    const run = await api<Run>(`/runs/${runId}`);
    await refresh(sessionId);
    setSavedCount(run.todo_ids.length);
    setPhase(
      run.status === "completed"
        ? "处理完成"
        : run.status === "partial"
          ? "部分完成"
          : "处理失败",
    );
    setBusy(false);
    setPending(null);
    localStorage.removeItem("assistant-pending");
  }

  function watch(operation: Pending) {
    stream.current?.close();
    const events = new EventSource(`/api/runs/${operation.request_id}/events`);
    stream.current = events;
    events.addEventListener("role", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setPhase(
        data.role === "learning"
          ? "学习 Agent 正在整理记忆"
          : "主 Agent 正在处理",
      );
    });
    events.addEventListener("memory_saved", () => setPhase("记忆保存已提交"));
    events.addEventListener("memory_updated", () =>
      setPhase("记忆更正已提交，正在读回"),
    );
    events.addEventListener("memory_deleted", () =>
      setPhase("记忆删除已提交，正在读回"),
    );
    events.addEventListener("tool_call", () => setPhase("正在处理待办或计划"));
    events.addEventListener("saved", () => setPhase("保存已提交，正在读回"));
    events.addEventListener("terminal", () => {
      void finish(operation.request_id, operation.session).catch(failed);
    });
    events.onerror = () => {
      events.close();
      setBusy(false);
      setError("连接中断。可重试同一请求，已提交的待办不会重复新增。");
    };
  }

  function failed(reason: unknown) {
    setError(reason instanceof Error ? reason.message : "服务暂时不可用");
    setBusy(false);
  }

  async function send(operation: Pending) {
    setBusy(true);
    setError("");
    setSavedCount(null);
    setPending(operation);
    localStorage.setItem("assistant-pending", JSON.stringify(operation));
    try {
      const run = await api<Run>(`/sessions/${operation.session}/messages`, {
        request_id: operation.request_id,
        content: operation.content,
        action: operation.action,
      });
      setInput("");
      await refresh(operation.session);
      if (run.status === "running") watch(operation);
      else await finish(run.id, operation.session);
    } catch (reason) {
      failed(reason);
    }
  }

  function act(content: string, action?: Action) {
    if (!session || busy || pending) return;
    void send({ session, request_id: crypto.randomUUID(), content, action });
  }

  useEffect(() => {
    if (!session) return;
    const update = () => {
      void refresh(session).catch(failed);
    };
    const timer = window.setInterval(update, 60000);
    window.addEventListener("focus", update);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", update);
    };
  }, [session]);

  useEffect(() => {
    let disposed = false;
    async function init() {
      const [status, summary] = await Promise.all([
        api<Health>("/health"),
        api<Overview>("/todos/overview"),
      ]);
      if (disposed) return;
      setHealth(status);
      setTodos(Object.values(summary.groups).flat());
      setOverview(summary);
      let id = localStorage.getItem("assistant-session");
      if (id) {
        try {
          await refresh(id);
          setSession(id);
        } catch {
          id = await newSession();
        }
      } else id = await newSession();
      const stored = localStorage.getItem("assistant-pending");
      if (stored) {
        const operation = JSON.parse(stored) as Pending;
        if (operation.session === id) {
          setPending(operation);
          setError("有一条请求待确认，可安全重试查看结果。");
        }
      }
    }
    void init().catch(failed);
    return () => {
      disposed = true;
      stream.current?.close();
    };
  }, []);

  return (
    <main>
      <header>
        <a className="brand" href="/">
          日常<span>生活助理</span>
        </a>
        <span className="connection">
          <i />
          {health?.model_configured ? "助理已连接" : "待配置模型"}
        </span>
      </header>
      <section className="intro">
        <p className="eyebrow">把想做的事，留在这里</p>
        <h1>给日常，留一点条理。</h1>
        <p>聊聊接下来的安排。每一项待办，都能再次找到。</p>
      </section>
      <div className="workspace">
        <section className="chat panel" aria-label="聊天">
          <div className="panel-head">
            <h2>和助理聊聊</h2>
            <button
              className="quiet"
              disabled={busy || !!pending}
              onClick={() => void newSession().catch(failed)}
            >
              ＋ 新会话
            </button>
          </div>
          <div className="messages" aria-live="polite">
            {!messages.length && (
              <div className="welcome">
                <span className="spark">✳</span>
                <h3>从一件小事开始</h3>
                <p>试着说：“明天我要学习 Python，还要整理书桌。”</p>
                <p className="hint">没有日期的事项会保留为「未安排」。</p>
              </div>
            )}
            {messages.map((m) => (
              <article key={m.id} className={`message ${m.role}`}>
                <div className="speaker">
                  {m.role === "user" ? "你" : "主 Agent"}
                </div>
                <p>{m.content}</p>
              </article>
            ))}
          </div>
          <div className="status" role="status">
            <span className={busy ? "pulse" : ""}>●</span> {phase}
            {savedCount !== null && (
              <span> · 本轮保存 {savedCount} 项待办</span>
            )}
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
              {pending && !busy && (
                <button onClick={() => void send(pending)}>重试同一请求</button>
              )}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (input.trim() && !busy && !pending)
                void send({
                  session,
                  request_id: crypto.randomUUID(),
                  content: input.trim(),
                });
            }}
          >
            <label htmlFor="message">你的安排或问题</label>
            <textarea
              id="message"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="请记录明天要做的事…"
              maxLength={8000}
              disabled={busy || !!pending}
            />
            <div className="composer-bottom">
              <small>只有明确安排才会保存为待办</small>
              <button
                className="primary"
                disabled={!session || !input.trim() || busy || !!pending}
              >
                发送 ↑
              </button>
            </div>
          </form>
          <section className="suggestions" aria-label="行动建议">
            <div className="panel-head">
              <h2>当天计划与行动建议</h2>
              <button
                className="quiet"
                disabled={!session || busy || !!pending}
                onClick={() => act("今天我该干什么？")}
              >
                生成当天计划
              </button>
            </div>
            <p className="list-note">建议需明确加入才会成为待办。</p>
            {suggestions.map((idea) => (
              <article key={idea.id} className="suggestion">
                <p>{idea.title}</p>
                <small>加入后安排在 {idea.scheduled_date}</small>
                <button
                  className="quiet"
                  disabled={busy || !!pending || !!idea.todo_id}
                  onClick={() =>
                    act(`把建议 ${idea.id} 加入待办`, {
                      tool: "accept_suggestion",
                      arguments: { suggestion_id: idea.id },
                    })
                  }
                >
                  {idea.todo_id ? "已加入" : "加入待办"}
                </button>
              </article>
            ))}
          </section>
        </section>
        <aside className="panel todos" aria-label="待办列表">
          <div className="panel-head">
            <h2>
              已保存的待办 <span className="count">{todos.length}</span>
            </h2>
            <button
              className="quiet"
              onClick={() => void refresh(session).catch(failed)}
            >
              刷新
            </button>
          </div>
          <p className="list-note">
            跨会话保留 · {health?.timezone || "Asia/Shanghai"} ·{" "}
            {overview?.today}
          </p>
          {!todos.length && (
            <div className="empty">
              <span>◎</span>
              <h3>这里还很轻盈</h3>
              <p>告诉助理你的安排，保存后会显示在这里。</p>
            </div>
          )}
          {Object.entries(groups).map(([key, label]) => (
            <section key={key} aria-label={label} className="todo-group">
              <h3>
                {label}{" "}
                <span className="count">
                  {overview?.groups[key]?.length || 0}
                </span>
              </h3>
              <ul>
                {(overview?.groups[key] || []).map((todo) => (
                  <TodoCard
                    key={todo.id}
                    todo={todo}
                    disabled={busy || !!pending}
                    act={act}
                  />
                ))}
              </ul>
            </section>
          ))}
          <p className="footnote">这里展示的是实际保存结果。</p>
        </aside>
      </div>
      <section className="panel memory-panel" aria-label="学到了什么">
        <div className="panel-head">
          <h2>学到了什么</h2>
          <span className="count">{memories.length}</span>
        </div>
        <p className="list-note">从普通聊天整理 · 保存内容与本次采用分开展示</p>
        <div className="memory-columns">
          <div>
            <h3>已保存的记忆</h3>
            {!memories.length && (
              <p>聊聊你的背景、资料偏好或今天的时间条件。</p>
            )}
            {memories.map((memory) => (
              <article className="memory-card" key={memory.id}>
                <h3>{memory.content}</h3>
                <p>
                  {memoryCategories[memory.category]} ·{" "}
                  {memory.scope === "task" ? "任务限定" : "一般范围"} ·{" "}
                  {memory.active
                    ? "生效中"
                    : memory.state === "conflict"
                      ? "冲突待澄清"
                      : "当前不生效"}
                </p>
                <small>
                  主题：{memory.topic}
                  <br />
                  生效：{memory.valid_from}
                  <br />
                  {memory.expires_at
                    ? `截止：${memory.expires_at}`
                    : memory.scope === "task"
                      ? "对应任务完成后失效"
                      : "持续有效"}
                </small>
                {memory.task_id && <p>对应待办：{memory.task_id}</p>}
                <MemoryEditor
                  key={memory.source.message_id}
                  memory={memory}
                  todos={todos}
                  disabled={busy || !!pending}
                  act={act}
                />
                <details>
                  <summary>来源表达与标识</summary>
                  <p>{memory.source.content}</p>
                  <small>
                    消息 {memory.source.message_id}
                    <br />
                    会话 {memory.source.session_id}
                    <br />
                    记忆 {memory.id}
                  </small>
                </details>
              </article>
            ))}
          </div>
          <div aria-label="本次记忆使用">
            <h3>本次加载与采用</h3>
            <p>
              {evidence?.learning === "failed"
                ? "本轮学习保存失败"
                : `本轮新保存 ${evidence?.saved_ids?.length || 0} 条记忆`}
            </p>
            <p>此次加载 {evidence?.loaded?.length || 0} 条生效记忆</p>
            {evidence?.loaded?.map((memory) => (
              <article className="memory-card" key={memory.id}>
                <p>{memory.content}</p>
                <small>来源消息 {memory.source.message_id}</small>
                <p>
                  采用说明：
                  {evidence.usage?.find((u) => u.memory_id === memory.id)
                    ?.reason || "未报告采用"}
                </p>
              </article>
            ))}
            <p className="footnote">采用说明由主 Agent 报告，效果尚未验证。</p>
          </div>
        </div>
      </section>
      <footer>
        日常 / 个人工作台 <span>从今天开始，慢慢做好每件事。</span>
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
