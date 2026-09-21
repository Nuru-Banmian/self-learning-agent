import { useEffect, useRef, useState } from "react";
import "./todo-panel.css";

export type Todo = {
  id: string;
  title: string;
  scheduled_date: string | null;
  status: string;
  source: { content: string; message_id: string; session_id: string };
  roadmap: { id: string; node_id: string } | null;
};

export type Overview = { today: string; groups: Record<string, Todo[]> };
export type TodoAction = {
  tool: "update_todo" | "complete_todo";
  arguments: Record<string, unknown>;
};

type TodoProps = {
  todos: Todo[];
  overview: Overview | null;
  disabled: boolean;
  onRefresh: () => void;
  act: (content: string, action: TodoAction) => void;
};

const filters = [
  { value: "pending", label: "未完成" },
  { value: "today", label: "今日" },
  { value: "overdue", label: "逾期" },
  { value: "upcoming", label: "未来" },
  { value: "unscheduled", label: "未安排" },
  { value: "completed", label: "已完成" },
  { value: "all", label: "全部" },
] as const;
type Filter = (typeof filters)[number]["value"];
const PAGE_SIZE = 8;

function category(todo: Todo, today: string): Filter {
  if (todo.status === "completed") return "completed";
  if (!todo.scheduled_date) return "unscheduled";
  if (todo.scheduled_date === today) return "today";
  if (todo.scheduled_date < today) return "overdue";
  return "upcoming";
}

function inFilter(todo: Todo, filter: Filter, today: string) {
  if (filter === "all") return true;
  if (filter === "pending") return todo.status !== "completed";
  return category(todo, today) === filter;
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
      <path
        d="m5 10 3.2 3.2L15 6.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TodoRow({
  todo,
  today,
  disabled,
  act,
}: Pick<TodoProps, "disabled" | "act"> & { todo: Todo; today: string }) {
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [title, setTitle] = useState(todo.title);
  const [date, setDate] = useState(todo.scheduled_date || "");
  const editButton = useRef<HTMLButtonElement>(null);
  const completed = todo.status === "completed";
  const group = category(todo, today);
  const stateLabel = completed
    ? "已完成"
    : group === "overdue"
      ? "逾期"
      : group === "today"
        ? "今日"
        : "待完成";

  function closeEditor() {
    setEditing(false);
    editButton.current?.focus();
  }

  return (
    <li className={`task-row${completed ? " task-row-completed" : ""}`}>
      <div className="task-row-main">
        <span
          className={`task-state-dot task-state-dot-${group}`}
          aria-hidden="true"
        >
          {completed && <CheckIcon />}
        </span>
        <div className="task-row-content">
          <button
            type="button"
            className="task-title-button"
            aria-expanded={expanded}
            aria-controls={`task-details-${todo.id}`}
            title={todo.title}
            onClick={() => setExpanded(!expanded)}
          >
            <span>{todo.title}</span>
            <svg
              className={
                expanded ? "task-chevron task-chevron-open" : "task-chevron"
              }
              viewBox="0 0 16 16"
              width="14"
              height="14"
              aria-hidden="true"
            >
              <path
                d="m5 6 3 3 3-3"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <div className="task-row-meta">
            {todo.scheduled_date ? (
              <time dateTime={todo.scheduled_date}>{todo.scheduled_date}</time>
            ) : (
              <span>未安排日期</span>
            )}
            <span className={`task-status task-status-${group}`}>
              {stateLabel}
            </span>
          </div>
        </div>
        <div className="task-row-actions">
          <button
            type="button"
            className="task-button task-button-quiet"
            ref={editButton}
            disabled={disabled}
            aria-label={`编辑待办：${todo.title}`}
            aria-expanded={editing}
            onClick={() => {
              setTitle(todo.title);
              setDate(todo.scheduled_date || "");
              setEditing(!editing);
            }}
          >
            编辑
          </button>
          {!completed && (
            <button
              type="button"
              className="task-button task-button-complete"
              disabled={disabled}
              aria-label={`标记完成：${todo.title}`}
              onClick={() =>
                act(`完成待办：${todo.title}`, {
                  tool: "complete_todo",
                  arguments: { todo_id: todo.id },
                })
              }
            >
              <CheckIcon />
              完成
            </button>
          )}
        </div>
      </div>
      {editing && (
        <form
          className="task-edit-form"
          aria-label={`编辑待办：${todo.title}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (disabled || !title.trim()) return;
            const arguments_: Record<string, unknown> = { todo_id: todo.id };
            if (title.trim() !== todo.title) arguments_.title = title.trim();
            if (date !== (todo.scheduled_date || ""))
              arguments_.date_text = date || null;
            if (Object.keys(arguments_).length > 1) {
              act(
                `修改待办：${todo.title} → ${title.trim()}，${date || "未安排"}`,
                { tool: "update_todo", arguments: arguments_ },
              );
            }
            closeEditor();
          }}
        >
          <label className="task-edit-title">
            标题
            <input
              aria-label="待办标题"
              required
              maxLength={200}
              value={title}
              autoFocus
              disabled={disabled}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label>
            安排日期
            <input
              aria-label="安排日期"
              type="date"
              value={date}
              disabled={disabled}
              onChange={(event) => setDate(event.target.value)}
            />
          </label>
          <div className="task-edit-bottom">
            <small>清空日期后归入未安排</small>
            <div className="task-edit-actions">
              <button
                type="button"
                className="task-button task-button-quiet"
                onClick={closeEditor}
              >
                取消
              </button>
              <button
                type="submit"
                className="task-button task-button-primary"
                disabled={disabled || !title.trim()}
              >
                保存修改
              </button>
            </div>
          </div>
        </form>
      )}
      <div
        className="task-details"
        id={`task-details-${todo.id}`}
        hidden={!expanded}
      >
        <p className="task-details-label">来源表达</p>
        <p className="task-source-content">{todo.source.content}</p>
        {todo.roadmap && (
          <a
            href={`#roadmap-${todo.roadmap.id}/${todo.roadmap.node_id}`}
            className="task-roadmap-link"
          >
            查看节点学习内容 <span aria-hidden="true">↗</span>
          </a>
        )}
        <details className="task-identifiers">
          <summary>查看来源与标识</summary>
          <p>
            待办 {todo.id}
            <br />
            消息 {todo.source.message_id}
            <br />
            会话 {todo.source.session_id}
          </p>
        </details>
      </div>
    </li>
  );
}

export function TodoPanel({
  todos,
  overview,
  disabled,
  onRefresh,
  act,
  initialFilter = "pending",
}: TodoProps & { initialFilter?: string }) {
  const [filter, setFilter] = useState<Filter>(
    () =>
      filters.find((item) => item.value === initialFilter)?.value || "pending",
  );
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const today = overview?.today || "";
  const pendingCount = todos.filter(
    (todo) => todo.status !== "completed",
  ).length;
  const search = query.trim().toLocaleLowerCase();
  const results = todos
    .filter(
      (todo) =>
        inFilter(todo, filter, today) &&
        todo.title.toLocaleLowerCase().includes(search),
    )
    .sort((a, b) => {
      const completedOrder =
        Number(a.status === "completed") - Number(b.status === "completed");
      return (
        completedOrder ||
        (a.scheduled_date || "9999").localeCompare(b.scheduled_date || "9999")
      );
    });
  const pageCount = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const start = (currentPage - 1) * PAGE_SIZE;
  const pageTodos = results.slice(start, start + PAGE_SIZE);
  const label = filters.find((item) => item.value === filter)?.label;

  useEffect(() => {
    setPage((previous) => Math.min(previous, pageCount));
  }, [pageCount]);

  return (
    <section className="task-library" aria-label="待办列表">
      <div className="task-library-heading">
        <div>
          <p className="task-eyebrow">每一件事，慢慢做好</p>
          <h2 id="todos-heading" tabIndex={-1}>
            我的待办
          </h2>
          <p className="task-heading-note">
            {overview
              ? `共 ${todos.length} 项 · ${pendingCount} 项未完成 · ${todos.length - pendingCount} 项已完成`
              : "正在读取已保存的安排"}
          </p>
        </div>
        <button
          type="button"
          className="task-button task-button-outlined"
          disabled={disabled}
          onClick={onRefresh}
        >
          刷新待办
        </button>
      </div>
      <div className="task-library-toolbar">
        <div className="task-filters" role="group" aria-label="筛选待办">
          {filters.map((item) => (
            <button
              type="button"
              className={`task-filter${filter === item.value ? " task-filter-active" : ""}`}
              key={item.value}
              aria-pressed={filter === item.value}
              onClick={() => {
                setFilter(item.value);
                setPage(1);
              }}
            >
              {item.label}
              <span>
                {overview
                  ? todos.filter((todo) => inFilter(todo, item.value, today))
                      .length
                  : "—"}
              </span>
            </button>
          ))}
        </div>
        <label className="task-search">
          <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden="true">
            <circle
              cx="8.5"
              cy="8.5"
              r="5.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <path
              d="m13 13 4 4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
          <input
            type="search"
            aria-label="搜索待办"
            placeholder="搜索待办…"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
      <div className="task-list-caption">
        <span>
          {label}
          <span className="task-caption-count">
            {overview ? results.length : "—"}
          </span>
        </span>
        <span>点击标题查看详情</span>
      </div>
      {!overview ? (
        <div className="task-empty-state" role="status">
          <span className="task-loading-indicator" aria-hidden="true" />
          <h3>正在载入待办</h3>
          <p>稍等一下，你的安排马上就好。</p>
        </div>
      ) : !results.length ? (
        <div className="task-empty-state" role="status">
          <span className="task-empty-mark" aria-hidden="true">
            <CheckIcon />
          </span>
          <h3>
            {search
              ? "没有找到匹配的待办"
              : !todos.length
                ? "从一件小事开始"
                : filter === "pending"
                  ? "所有待办都已完成"
                  : `暂无${label}待办`}
          </h3>
          <p>
            {search
              ? "换个关键词试试，或清除搜索查看当前清单。"
              : !todos.length
                ? "在对话中告诉助理你的安排，保存后会显示在这里。"
                : filter === "pending"
                  ? "给自己一点空闲，也可以查看已经完成的事。"
                  : "切换分类，看看其他安排。"}
          </p>
          {search && (
            <button
              type="button"
              className="task-button task-button-outlined"
              onClick={() => {
                setQuery("");
                setPage(1);
              }}
            >
              清除搜索
            </button>
          )}
        </div>
      ) : (
        <ul className="task-list">
          {pageTodos.map((todo) => (
            <TodoRow
              key={todo.id}
              todo={todo}
              today={today}
              disabled={disabled}
              act={act}
            />
          ))}
        </ul>
      )}
      {overview && results.length > 0 && (
        <nav className="task-pagination" aria-label="待办分页">
          <p aria-live="polite">
            第 {start + 1}–{Math.min(start + PAGE_SIZE, results.length)} 项，共{" "}
            {results.length} 项
          </p>
          <div>
            <button
              type="button"
              className="task-button task-button-outlined"
              disabled={currentPage === 1}
              onClick={() => setPage(currentPage - 1)}
            >
              上一页
            </button>
            <span aria-label={`第 ${currentPage} 页，共 ${pageCount} 页`}>
              {currentPage} / {pageCount}
            </span>
            <button
              type="button"
              className="task-button task-button-outlined"
              disabled={currentPage === pageCount}
              onClick={() => setPage(currentPage + 1)}
            >
              下一页
            </button>
          </div>
        </nav>
      )}
    </section>
  );
}

export function TodoOverview({
  todos,
  overview,
  disabled,
  onRefresh,
  act,
  onOpen,
}: TodoProps & { onOpen: (filter?: string) => void }) {
  const today = overview?.today || "";
  const pending = todos.filter((todo) => todo.status !== "completed");
  const todayCount = pending.filter(
    (todo) => category(todo, today) === "today",
  ).length;
  const overdueCount = pending.filter(
    (todo) => category(todo, today) === "overdue",
  ).length;
  const priorities: Record<string, number> = {
    today: 0,
    overdue: 1,
    unscheduled: 2,
    upcoming: 3,
  };
  const preview = [...pending]
    .sort(
      (a, b) =>
        priorities[category(a, today)] - priorities[category(b, today)] ||
        (a.scheduled_date || "9999").localeCompare(b.scheduled_date || "9999"),
    )
    .slice(0, 5);

  return (
    <aside className="task-overview" aria-label="待办概览">
      <div className="task-overview-heading">
        <h2>接下来</h2>
        <button
          type="button"
          className="task-button task-button-quiet"
          disabled={disabled}
          onClick={onRefresh}
        >
          刷新
        </button>
      </div>
      <div className="task-overview-stats">
        <button
          type="button"
          onClick={() => onOpen("today")}
          aria-label={`查看今日待办 ${overview ? todayCount : ""} 项`}
        >
          <span>今日待办</span>
          <strong>{overview ? todayCount : "—"}</strong>
        </button>
        <button
          type="button"
          className={overdueCount > 0 ? "task-stat-overdue" : ""}
          onClick={() => onOpen("overdue")}
          aria-label={`查看逾期待办 ${overview ? overdueCount : ""} 项`}
        >
          <span>逾期未完成</span>
          <strong>{overview ? overdueCount : "—"}</strong>
        </button>
      </div>
      {!overview ? (
        <p className="task-overview-empty" role="status">
          正在载入待办…
        </p>
      ) : !pending.length ? (
        <div className="task-overview-empty">
          <p>{todos.length ? "当前没有未完成待办" : "还没有保存的待办"}</p>
          <span>
            {todos.length
              ? "已完成的事，都好好保留着。"
              : "聊聊今天想做的事吧。"}
          </span>
        </div>
      ) : (
        <>
          <p className="task-overview-caption">
            {todayCount + overdueCount > 0
              ? "先关注这些安排"
              : "从这些安排开始"}
            <span>未完成 {pending.length} 项</span>
          </p>
          <ul className="task-overview-list">
            {preview.map((todo) => {
              const group = category(todo, today);
              return (
                <li key={todo.id}>
                  <button
                    type="button"
                    className="task-overview-check"
                    disabled={disabled}
                    aria-label={`标记完成：${todo.title}`}
                    onClick={() =>
                      act(`完成待办：${todo.title}`, {
                        tool: "complete_todo",
                        arguments: { todo_id: todo.id },
                      })
                    }
                  >
                    <CheckIcon />
                  </button>
                  <button
                    type="button"
                    className="task-overview-item"
                    title={todo.title}
                    onClick={() => onOpen(group)}
                  >
                    <span>{todo.title}</span>
                    <small
                      className={group === "overdue" ? "task-overdue-date" : ""}
                    >
                      {group === "today"
                        ? "今天"
                        : group === "overdue"
                          ? `逾期 · ${todo.scheduled_date}`
                          : todo.scheduled_date || "未安排日期"}
                    </small>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <button
        type="button"
        className="task-open-library"
        onClick={() => onOpen("pending")}
      >
        <span>
          打开完整清单
          {overview && pending.length > 5
            ? ` · ${pending.length} 项未完成`
            : ""}
        </span>
        <span aria-hidden="true">→</span>
      </button>
    </aside>
  );
}
