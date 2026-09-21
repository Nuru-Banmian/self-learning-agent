import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { WeatherPanel, type WeatherEvidence } from "./WeatherPanel";
import { EvidencePanel } from "./EvidencePanel";
import { RoadmapPanel, type RoadmapSummary } from "./RoadmapPanel";

type Message = { id: string; role: string; content: string };
type Todo = {
  id: string;
  title: string;
  scheduled_date: string | null;
  status: string;
  source: { content: string; message_id: string; session_id: string };
  roadmap: { id: string; node_id: string } | null;
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
  session_id: string;
  content: string;
  action: Action | null;
  error: string | null;
  queue_position: number;
  retryable: boolean;
  retry_of: string | null;
  retry_run_id: string | null;
  events: { seq: number; kind: string; data: Record<string, unknown> }[];
  messages: Message[];
  status: string;
  reply: string;
  todo_ids: string[];
  memory: MemoryEvidence;
  research: ResearchEvidence;
  weather: WeatherEvidence;
};
type ResearchEvidence = {
  status?: string;
  task?: { query: string; read_body: boolean };
  sources?: {
    id: string;
    title: string;
    url: string;
    snippet: string;
    material_type: string;
    body: string | null;
    body_truncated?: boolean;
  }[];
  calls?: {
    tool: string;
    status: string;
    elapsed_ms?: number;
    http_status?: number;
  }[];
  gaps?: string[];
};
const researchStatuses: Record<string, string> = {
  running: "进行中",
  success: "成功",
  empty: "无结果",
  partial: "部分完成",
  error: "失败",
};

function ResearchPanel({ research }: { research: ResearchEvidence | null }) {
  if (!research?.status) return null;
  return (
    <section className="suggestions research" aria-label="外部资料与执行记录">
      <h2>外部资料与执行记录</h2>
      <p>执行 Agent · {researchStatuses[research.status] || research.status}</p>
      <p>查询：{research.task?.query}</p>
      <p className="list-note">
        资料仅供参考；行动建议需要明确加入才会成为待办。
      </p>
      {research.gaps?.map((gap, i) => (
        <p className="error" key={i}>
          {gap}
        </p>
      ))}
      {research.sources?.map((source) => (
        <article className="suggestion" key={source.id}>
          <a href={source.url} target="_blank" rel="noreferrer">
            [{source.id}] {source.title}
          </a>
          <p>{source.snippet}</p>
          <small>
            {source.material_type === "body"
              ? "已取得正文"
              : "仅搜索摘要，未读取正文"}
          </small>
          {source.body && (
            <details>
              <summary>
                查看已取得正文{source.body_truncated ? "（截取片段）" : ""}
              </summary>
              <p className="source-body">{source.body}</p>
            </details>
          )}
        </article>
      ))}
      <details>
        <summary>实际工具调用 · {research.calls?.length || 0} 次</summary>
        {research.calls?.map((call, i) => (
          <p key={i}>
            {call.tool === "iqs_search" ? "IQS 搜索" : "IQS 正文读取"} ·{" "}
            {researchStatuses[call.status] || call.status} ·{" "}
            {call.elapsed_ms ?? "…"} ms
            {call.http_status ? ` · HTTP ${call.http_status}` : ""}
          </p>
        ))}
      </details>
    </section>
  );
}
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
    | "accept_roadmap_node"
    | "update_memory"
    | "delete_memory";
  arguments: Record<string, unknown>;
};
type Pending = {
  session: string;
  request_id: string;
  content: string;
  action?: Action;
  retry_of?: string;
};
const activeRun = (run: Run) =>
  run.status === "running" || run.status === "queued";
function runPhase(run: Run) {
  if (run.status === "queued")
    return `排队中 · 前面还有 ${run.queue_position} 轮`;
  if (run.status === "running") return "正在处理";
  if (run.status === "completed") return "处理完成";
  if (run.status === "partial") return "部分完成";
  return run.error === "interrupted" ? "处理已中断" : "处理失败";
}
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
      {todo.roadmap && <a href={`#roadmap-${todo.roadmap.id}/${todo.roadmap.node_id}`} className="roadmap-link">查看节点学习内容</a>}
    </li>
  );
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? { signal: AbortSignal.timeout(10000) }
      : {
          method: "POST",
          signal: AbortSignal.timeout(10000),
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试。",
      response.status,
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
  const [roadmaps, setRoadmaps] = useState<RoadmapSummary[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("准备就绪");
  const [error, setError] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [savedCount, setSavedCount] = useState<number | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [evidence, setEvidence] = useState<MemoryEvidence | null>(null);
  const [research, setResearch] = useState<ResearchEvidence | null>(null);
  const [weather, setWeather] = useState<WeatherEvidence | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const stream = useRef<EventSource | null>(null);
  const tracking = useRef<string | null>(null);
  const currentSession = useRef("");
  const refreshVersion = useRef(0);

  async function refresh(id: string) {
    const version = ++refreshVersion.current;
    const isCurrent = () =>
      currentSession.current === id && refreshVersion.current === version;
    try {
      const [data, summary, ideas, savedMemories, savedRoadmaps] = await Promise.all([
        api<{
          messages: Message[];
          latest_run_id: string | null;
          run_ids: string[];
        }>(`/sessions/${id}`),
        api<Overview>("/todos/overview"),
        api<Suggestion[]>(`/sessions/${id}/suggestions`),
        api<Memory[]>("/memories"),
        api<RoadmapSummary[]>("/roadmaps"),
      ]);
      const history = await Promise.all(
        data.run_ids.map((runId) => api<Run>(`/runs/${runId}`)),
      );
      // Commit the entire view only if this is still its newest refresh.
      if (!isCurrent()) return undefined;
      setTodos(Object.values(summary.groups).flat());
      setOverview(summary);
      setSuggestions(ideas);
      setMemories(savedMemories);
      setRoadmaps(savedRoadmaps);
      // A run may finish after the session snapshot; include its committed reply.
      const byId = new Map(
        data.messages.map((message) => [message.id, message]),
      );
      for (const run of history)
        for (const message of run.messages) byId.set(message.id, message);
      setMessages([...byId.values()]);
      setRuns(history);
      const latest = history.at(-1) || null;
      if (latest) {
        setEvidence(latest.memory);
        setResearch(latest.research);
        setWeather(latest.weather);
        setPhase(runPhase(latest));
        setSavedCount(activeRun(latest) ? null : latest.todo_ids.length);
      } else {
        setEvidence(null);
        setResearch(null);
        setWeather(null);
      }
      return latest;
    } catch (reason) {
      if (!isCurrent()) return undefined;
      throw reason;
    }
  }

  async function newSession() {
    const created = await api<{ id: string }>("/sessions", {});
    currentSession.current = created.id;
    refreshVersion.current++;
    tracking.current = null;
    stream.current?.close();
    localStorage.setItem("assistant-session", created.id);
    setSession(created.id);
    setMessages([]);
    setSuggestions([]);
    setPhase("准备就绪");
    setSavedCount(null);
    setEvidence(null);
    setResearch(null);
    setWeather(null);
    setRuns([]);
    await refresh(created.id);
    return created.id;
  }

  async function finish(runId: string, sessionId: string) {
    const run = await api<Run>(`/runs/${runId}`);
    if (tracking.current !== runId) return;
    const latest = await refresh(sessionId);
    if (tracking.current !== runId || latest === undefined) return;
    if (activeRun(run)) return;
    if (latest && activeRun(latest)) {
      follow(latest);
      return;
    }
    stream.current?.close();
    tracking.current = null;
    setBusy(false);
    setPending(null);
    setError("");
    localStorage.removeItem("assistant-pending");
  }

  function follow(run: Run) {
    if (currentSession.current !== run.session_id) return;
    const operation = {
      session: run.session_id,
      request_id: run.id,
      content: run.content,
      action: run.action || undefined,
    };
    tracking.current = run.id;
    setPhase(runPhase(run));
    setPending(operation);
    localStorage.setItem("assistant-pending", JSON.stringify(operation));
    if (activeRun(run)) {
      setBusy(true);
      watch(operation);
    } else void finish(run.id, run.session_id).catch(failed);
  }

  function watch(operation: Pending) {
    stream.current?.close();
    const events = new EventSource(`/api/runs/${operation.request_id}/events`);
    stream.current = events;
    events.addEventListener("queued", () => setPhase("排队中，等待前一轮完成"));
    events.addEventListener("role", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setPhase(
        data.role === "learning"
          ? "学习 Agent 正在整理记忆"
          : data.role === "execution"
            ? "执行 Agent 正在查询外部信息"
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
    events.addEventListener("tool_call", (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setPhase(
        data.role === "execution"
          ? data.tool === "qweather_city"
            ? "执行 Agent 正在确认目的地"
            : data.tool === "qweather_daily"
              ? "执行 Agent 正在查询天气"
              : data.tool === "iqs_read_page"
                ? "执行 Agent 正在读取正文"
                : "执行 Agent 正在搜索"
          : "正在处理待办或计划",
      );
    });
    events.addEventListener("research", (event) =>
      setResearch(JSON.parse((event as MessageEvent).data)),
    );
    events.addEventListener("weather", (event) =>
      setWeather(JSON.parse((event as MessageEvent).data)),
    );
    events.addEventListener("saved", () => setPhase("保存已提交，正在读回"));
    events.addEventListener("terminal", () => {
      void finish(operation.request_id, operation.session).catch(failed);
    });
    events.onerror = () => {
      events.close();
      setBusy(false);
      setPhase("连接中断，正在查询已保存状态");
      setError("连接中断，正在重新读取状态。也可重试同一请求，不会重复写入。");
    };
  }

  function failed(reason: unknown) {
    setError(reason instanceof Error ? reason.message : "服务暂时不可用");
    setBusy(false);
  }

  async function send(operation: Pending) {
    refreshVersion.current++;
    setBusy(true);
    setError("");
    setSavedCount(null);
    setPending(operation);
    tracking.current = operation.request_id;
    setResearch(null);
    localStorage.setItem("assistant-pending", JSON.stringify(operation));
    try {
      const run = operation.retry_of
        ? await api<Run>(`/runs/${operation.retry_of}/retry`, {})
        : await api<Run>(`/sessions/${operation.session}/messages`, {
            request_id: operation.request_id,
            content: operation.content,
            action: operation.action,
          });
      setInput("");
      await refresh(operation.session);
      follow(run);
    } catch (reason) {
      failed(reason);
    }
  }

  function act(content: string, action?: Action) {
    if (!session || busy || pending) return;
    void send({ session, request_id: crypto.randomUUID(), content, action });
  }

  useEffect(() => {
    if (!pending) return;
    let disposed = false;
    let polling = false;
    const timer = window.setInterval(async () => {
      if (polling) return;
      polling = true;
      try {
        if (pending.retry_of) {
          const original = await api<Run>(`/runs/${pending.retry_of}`);
          if (original.retry_run_id && !disposed) {
            const child = await api<Run>(`/runs/${original.retry_run_id}`);
            if (!disposed) follow(child);
          }
        } else if (!disposed) await finish(pending.request_id, pending.session);
      } catch {
        if (!disposed) {
          setBusy(false);
          setPhase("连接中断，等待重新读取状态");
          setError("暂时无法读取状态，正在重新连接；可安全重试同一请求。");
        }
      } finally {
        polling = false;
      }
    }, 2000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [pending]);

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
      let latest: Run | null | undefined = null;
      if (id) {
        try {
          currentSession.current = id;
          latest = await refresh(id);
          if (currentSession.current !== id || disposed) return;
          setSession(id);
        } catch (reason) {
          if (reason instanceof ApiError && reason.status === 404)
            id = await newSession();
          else throw reason;
        }
      } else id = await newSession();
      const stored = localStorage.getItem("assistant-pending");
      if (stored) {
        const operation = JSON.parse(stored) as Pending;
        if (operation.session === id) {
          setPending(operation);
          tracking.current = operation.request_id;
          setError("有一条请求待确认，可安全重试查看结果。");
          if (!operation.retry_of) {
            try {
              follow(await api<Run>(`/runs/${operation.request_id}`));
            } catch (reason) {
              failed(reason);
            }
          }
        }
      } else if (latest && activeRun(latest)) follow(latest);
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
              {!pending && (
                <button onClick={() => window.location.reload()}>
                  重新连接
                </button>
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
          <section className="suggestions" aria-label="请求执行记录">
            <h2>请求执行记录</h2>
            <p className="list-note">
              状态与保存结果来自服务端；回复文本不代表整轮完成。
            </p>
            {runs.map((run) => (
              <article className="suggestion" key={run.id}>
                <p>{run.content}</p>
                <strong>{runPhase(run)}</strong>
                <p>已提交待办变更 {run.todo_ids.length} 项</p>
                {run.retry_of && <small>这是一次重试，原记录保留。</small>}
                {run.retryable && !run.retry_run_id && (
                  <button
                    className="quiet"
                    disabled={busy || !!pending}
                    onClick={() =>
                      void send({
                        session,
                        request_id: run.id,
                        content: run.content,
                        retry_of: run.id,
                      })
                    }
                  >
                    重试未完成处理
                  </button>
                )}
                {run.retry_run_id && <p>已有重试记录，请查看后续结果。</p>}
                <details>
                  <summary>查看回复与执行记录</summary>
                  <p>{run.reply || "尚无最终回复"}</p>
                  <small>请求 {run.id}</small>
                  <ol>
                    {run.events.map((event) => (
                      <li key={event.seq}>
                        {event.seq} · {event.kind}
                        {typeof event.data.role === "string"
                          ? ` · ${event.data.role}`
                          : ""}
                        {typeof event.data.tool === "string"
                          ? ` · ${event.data.tool}`
                          : ""}
                        {typeof event.data.status === "string"
                          ? ` · ${event.data.status}`
                          : ""}
                      </li>
                    ))}
                  </ol>
                </details>
              </article>
            ))}
          </section>
          <ResearchPanel research={research} />
          <RoadmapPanel roadmaps={roadmaps} disabled={!session || busy || !!pending} act={act} />
          <WeatherPanel weather={weather} />
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
            <p className="list-note">
              可逐项选择加入；未选择的建议不会保存为待办。
            </p>
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
      <EvidencePanel runs={runs} />
      <footer>
        日常 / 个人工作台 <span>从今天开始，慢慢做好每件事。</span>
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
