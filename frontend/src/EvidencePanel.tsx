import { useEffect, useState } from "react";

type MemorySnapshot = {
  id: string;
  content: string;
  source: { content?: string; message_id: string; session_id: string };
};
type Checkpoint = {
  id: string;
  criterion: string;
  observation: string;
  status: "passed" | "failed" | "unverified";
  baseline_run_id: string | null;
  created_at: string;
  reviewer: string;
};
type EvidenceRun = {
  id: string;
  session_id: string;
  content: string;
  reply: string;
  status: string;
  model_calls: number;
  execution: { model?: string; enable_thinking?: boolean };
  memory: {
    learning?: string;
    saved_ids?: string[];
    saved?: MemorySnapshot[];
    loaded?: MemorySnapshot[];
    usage?: { memory_id: string; reason: string }[];
  };
  checkpoints: Checkpoint[];
};
const labels = { passed: "通过", failed: "失败", unverified: "未验证" };

async function readRun(id: string): Promise<EvidenceRun> {
  const response = await fetch(`/api/runs/${encodeURIComponent(id)}`, {
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("未找到请求记录，请检查请求标识。");
  return response.json();
}

function Snapshot({ run }: { run: EvidenceRun }) {
  return (
    <article className="memory-card evidence-snapshot">
      <h3>{run.content}</h3>
      <p>
        执行状态：{run.status} · 实际模型调用 {run.model_calls} 次
      </p>
      <p>
        配置模型：{run.execution.model || "旧记录未记录"} · 深度思考：
        {run.execution.enable_thinking === false ? "关闭" : "未记录"}
      </p>
      <small>
        请求 {run.id}
        <br />
        会话 {run.session_id}
      </small>
      <h4>已保存</h4>
      <p>
        {run.memory.learning === "failed"
          ? "本轮学习失败"
          : `本轮新保存 ${run.memory.saved_ids?.length || 0} 条`}
      </p>
      {(run.memory.saved || []).map((memory) => (
        <p key={memory.id}>
          {memory.content}
          <br />
          <small>
            记忆 {memory.id} · 来源消息 {memory.source.message_id}
            <br />
            来源会话 {memory.source.session_id}
            <br />
            原文：{memory.source.content}
          </small>
        </p>
      ))}
      {!!run.memory.saved_ids?.length && !run.memory.saved && (
        <p>旧记录仅保留保存标识，未记录内容快照。</p>
      )}
      <h4>本轮加载与采用说明</h4>
      <p>加载 {run.memory.loaded?.length || 0} 条；以下为执行当时快照。</p>
      {(run.memory.loaded || []).map((memory) => (
        <div key={memory.id}>
          <p>{memory.content}</p>
          <small>
            记忆 {memory.id}
            <br />
            来源消息 {memory.source.message_id}
            <br />
            来源会话 {memory.source.session_id}
          </small>
          <p>
            采用说明：
            {run.memory.usage?.find((u) => u.memory_id === memory.id)?.reason ||
              "未报告采用"}
          </p>
        </div>
      ))}
      <h4>可观察回答</h4>
      <p className="evidence-reply">{run.reply || "尚无最终回答"}</p>
    </article>
  );
}

function Review({ id }: { id: string }) {
  const [run, setRun] = useState<EvidenceRun | null>(null);
  const [baseline, setBaseline] = useState<EvidenceRun | null>(null);
  const [baselineId, setBaselineId] = useState("");
  const [criterion, setCriterion] = useState("");
  const [observation, setObservation] = useState("");
  const [status, setStatus] = useState<Checkpoint["status"]>("unverified");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void readRun(id)
      .then((value) => {
        if (active) {
          setRun(value);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(String(e.message));
      });
    return () => {
      active = false;
    };
  }, [id, revision]);
  useEffect(() => {
    let active = true;
    setBaseline(null);
    if (baselineId)
      void readRun(baselineId)
        .then((value) => {
          if (active) setBaseline(value);
        })
        .catch((e) => {
          if (active) setError(String(e.message));
        });
    return () => {
      active = false;
    };
  }, [baselineId]);
  const comparable =
    baseline &&
    run &&
    baseline.id !== run.id &&
    !!run.execution.model &&
    baseline.execution.model === run.execution.model &&
    baseline.model_calls > 0 &&
    run.model_calls > 0;
  return (
    <div>
      {error && <p role="alert">{error}</p>}
      <button className="quiet" onClick={() => setRevision((v) => v + 1)}>
        刷新证据
      </button>
      {run && (
        <>
          <div className="evidence-comparison">
            {baseline && <Snapshot run={baseline} />}
            <Snapshot run={run} />
          </div>
          <label>
            对比基线请求标识（可跨会话）
            <input
              value={baselineId}
              onChange={(e) => setBaselineId(e.target.value.trim())}
            />
          </label>
          {baseline && (
            <p>
              {comparable
                ? "模型快照一致；请核对任务差异与具体行为。左侧为基线，右侧为本轮。"
                : "不能作同模型对比：请选择另一条具有同模型调用的记录。"}
            </p>
          )}
          <h3>检查点结果</h3>
          <p>
            模型采用说明不代表验证通过。以下为人工核验记录，仅证明所列检查点，不代表首版整体验收。
          </p>
          {!run.checkpoints.length && <p>未验证：尚无人工检查点。</p>}
          {run.checkpoints.map((check) => (
            <article className="memory-card" key={check.id}>
              <strong>{labels[check.status]} · 人工核验</strong>
              <p>{check.criterion}</p>
              <p>{check.observation}</p>
              <small>{check.created_at}</small>
              {check.baseline_run_id && (
                <button
                  className="quiet"
                  onClick={() => setBaselineId(check.baseline_run_id!)}
                >
                  查看此检查点的对比基线
                </button>
              )}
            </article>
          ))}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setSaving(true);
              setError("");
              try {
                const response = await fetch(
                  `/api/runs/${encodeURIComponent(id)}/checkpoints`,
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    signal: AbortSignal.timeout(10000),
                    body: JSON.stringify({
                      id: crypto.randomUUID(),
                      criterion,
                      observation,
                      status,
                      baseline_run_id: baselineId || null,
                    }),
                  },
                );
                if (!response.ok) {
                  const body = await response.json();
                  throw new Error(
                    typeof body.detail === "string"
                      ? body.detail
                      : "请完整填写检查点与观察结果。",
                  );
                }
                setRun(await readRun(id));
                setCriterion("");
                setObservation("");
                setStatus("unverified");
              } catch (e) {
                setError(
                  e instanceof Error ? e.message : "保存失败，请刷新核对。",
                );
              } finally {
                setSaving(false);
              }
            }}
          >
            <label>
              检查点（具体任务标准）
              <input
                required
                maxLength={500}
                value={criterion}
                onChange={(e) => setCriterion(e.target.value)}
                placeholder="例如：新主题采用官方资料，建议未自动写入待办"
              />
            </label>
            <label>
              观察结果与依据
              <textarea
                required
                maxLength={2000}
                value={observation}
                onChange={(e) => setObservation(e.target.value)}
                placeholder="记录实际来源、步骤、加载情况或待办变化；不能只写模型说已采用"
              />
            </label>
            <label>
              人工判定
              <select
                value={status}
                onChange={(e) =>
                  setStatus(e.target.value as Checkpoint["status"])
                }
              >
                {Object.entries(labels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <button
              disabled={
                saving ||
                ["running", "queued"].includes(run.status) ||
                (!!baselineId && !comparable)
              }
            >
              保存人工检查点
            </button>
          </form>
        </>
      )}
    </div>
  );
}

export function EvidencePanel({
  runs,
}: {
  runs: { id: string; content: string }[];
}) {
  const [selected, setSelected] = useState(
    () => localStorage.getItem("assistant-evidence-run") || "",
  );
  const [lookup, setLookup] = useState(selected);
  function choose(id: string) {
    setSelected(id);
    setLookup(id);
    localStorage.setItem("assistant-evidence-run", id);
  }
  return (
    <section className="panel evidence-panel" aria-label="学习迁移证据">
      <h2>学习迁移证据</h2>
      <p>
        保存、加载、采用说明与人工检查点分别记录。选取历史回答，在新会话间对比，刷新后可继续查看。
      </p>
      <label>
        当前会话回答
        <select
          value={runs.some((r) => r.id === selected) ? selected : ""}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">选择请求</option>
          {runs.map((run) => (
            <option key={run.id} value={run.id}>
              {run.content}
            </option>
          ))}
        </select>
      </label>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (lookup.trim()) choose(lookup.trim());
        }}
      >
        <label>
          历史请求标识
          <input
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
            required
          />
        </label>
        <button>查看历史证据</button>
      </form>
      {selected && <Review key={selected} id={selected} />}
    </section>
  );
}
