import { Bot, Loader2, RefreshCw, Save, Send, Sparkles, Square, Undo2 } from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, imageUrl } from "./api";
import { assignmentOf, blankAnnotation, buildInstruction, isTwoSubject, setCaseMode, toggleIdentity } from "./annotation";
import "./styles.css";
import { CASE_TYPES } from "./types";
import type {
  AppSettings,
  AutomationMode,
  AutomationStatus,
  CaseType,
  LocalStatus,
  QueueItem,
  RcrAnnotation,
  Task,
  TaskImage
} from "./types";

type QueueFilter = "pending" | "review" | "drafted" | "submitted" | "all";
type Toast = { message: string; type: "success" | "error" | "info" };

const STATUS_LABEL: Record<string, string> = {
  generated: "AI draft",
  needs_review: "Needs review",
  draft_saved: "Draft saved",
  submitted: "Submitted",
  failed: "Failed",
  reviewed: "Reviewed"
};

function queueState(item: QueueItem): string {
  if (item.status === "SUBMITTED") return "submitted";
  return item.localStatus ?? "todo";
}

function matchesFilter(item: QueueItem, filter: QueueFilter): boolean {
  const state = queueState(item);
  if (filter === "all") return true;
  if (filter === "submitted") return state === "submitted";
  if (filter === "review") return state === "needs_review" || state === "failed" || state === "generated";
  if (filter === "drafted") return state === "draft_saved" || state === "reviewed";
  return state !== "submitted";
}

/** "test__<query>__<target>" → "<query tail> → <target tail>" so neighbouring tasks stay distinguishable. */
function shortId(sampleId: string): string {
  const [, query = sampleId, target = ""] = sampleId.split("__");
  const tail = (value: string) => value.split("_").pop() ?? value;
  return target ? `${tail(query)} → ${tail(target)}` : tail(query);
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function App() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [filter, setFilter] = useState<QueueFilter>("pending");
  const [caseFilter, setCaseFilter] = useState<"all" | CaseType>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [annotation, setAnnotation] = useState<RcrAnnotation | null>(null);
  const [localStatus, setLocalStatus] = useState<LocalStatus | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [concerns, setConcerns] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [activeSubject, setActiveSubject] = useState(1);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [mode, setMode] = useState<AutomationMode>("all_open");
  const [automationCase, setAutomationCase] = useState<CaseType>("INDIVIDUAL");
  const [limit, setLimit] = useState(10);
  const loadSeq = useRef(0);

  const submitted = task?.status === "SUBMITTED";
  const instruction = useMemo(() => (annotation ? buildInstruction(annotation) : ""), [annotation]);
  const visibleQueue = useMemo(
    () => queue.filter((item) => matchesFilter(item, filter) && (caseFilter === "all" || item.case_type === caseFilter)),
    [queue, filter, caseFilter]
  );
  const submittedCount = queue.filter((item) => item.status === "SUBMITTED").length;

  const showToast = useCallback((message: string, type: Toast["type"] = "info") => {
    setToast({ message, type });
    setTimeout(() => setToast((current) => (current?.message === message ? null : current)), 4500);
  }, []);

  const loadQueue = useCallback(async () => {
    try {
      setQueue((await api.queue()).tasks);
    } catch (error) {
      showToast(errorText(error), "error");
    }
  }, [showToast]);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await api.automationStatus());
    } catch {
      /* Backend may be restarting; the next poll recovers. */
    }
  }, []);

  useEffect(() => {
    void api.settings().then(setSettings).catch((error) => showToast(errorText(error), "error"));
    void loadQueue();
    void refreshStatus();
  }, [loadQueue, refreshStatus, showToast]);

  useEffect(() => {
    if (!status?.running) return;
    const timer = setInterval(() => {
      void refreshStatus();
      void loadQueue();
    }, 5000);
    return () => clearInterval(timer);
  }, [status?.running, loadQueue, refreshStatus]);

  const selectTask = useCallback(
    async (sampleId: string) => {
      const seq = ++loadSeq.current;
      setSelected(sampleId);
      setBusy("Loading task");
      try {
        const detail = await api.task(sampleId);
        if (seq !== loadSeq.current) return;
        const local = detail.local;
        const editable = detail.task.status !== "SUBMITTED";
        const initial =
          (editable ? local?.annotation : null) ?? detail.annotation ?? blankAnnotation(detail.task);
        setTask(detail.task);
        setAnnotation(initial);
        setLocalStatus(local?.status ?? null);
        setIssues([]);
        setConcerns(editable ? local?.concerns ?? [] : []);
        setActiveSubject(1);
        setNotes("");
      } catch (error) {
        if (seq === loadSeq.current) showToast(errorText(error), "error");
      } finally {
        if (seq === loadSeq.current) setBusy(null);
      }
    },
    [showToast]
  );

  // Live validation of whatever is in the editor.
  useEffect(() => {
    if (!task || !annotation || submitted) return;
    const timer = setTimeout(() => {
      api
        .validate(task.sample_id, annotation)
        .then((result) => setIssues(result.issues))
        .catch(() => undefined);
    }, 500);
    return () => clearTimeout(timer);
  }, [task, annotation, submitted]);

  function edit(update: (current: RcrAnnotation) => RcrAnnotation) {
    setAnnotation((current) => (current && !submitted ? update(current) : current));
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    try {
      await action();
    } catch (error) {
      showToast(errorText(error), "error");
    } finally {
      setBusy(null);
    }
  }

  const generate = () =>
    run("Generating with AI", async () => {
      if (!task) return;
      const result = await api.generate(task.sample_id, notes);
      setAnnotation(result.annotation);
      setIssues(result.issues);
      setConcerns(result.concerns);
      const flagged = result.issues.length + result.concerns.length > 0;
      setLocalStatus(flagged ? "needs_review" : "generated");
      showToast(flagged ? "AI draft ready, needs review" : "AI draft ready", "success");
      void loadQueue();
    });

  const push = (submit: boolean) =>
    run(submit ? "Submitting" : "Saving draft", async () => {
      if (!task || !annotation) return;
      const result = await (submit ? api.submit : api.saveDraft)(task.sample_id, annotation);
      if (result.status === "blocked") {
        setIssues(result.issues ?? []);
        showToast("Blocked: fix the issues first", "error");
        return;
      }
      if (result.task) setTask(result.task);
      setLocalStatus(result.status);
      setIssues([]);
      setConcerns([]);
      showToast(submit ? "Submitted to RCR" : "Draft saved to RCR", "success");
      await loadQueue();
      if (submit) {
        const next = queue.find(
          (item) => item.sample_id !== task.sample_id && matchesFilter(item, "pending") && item.status !== "SUBMITTED"
        );
        if (next) await selectTask(next.sample_id);
      }
    });

  const reopen = () =>
    run("Reopening", async () => {
      if (!task) return;
      const result = await api.reopen(task.sample_id);
      setTask({ ...task, ...result.task });
      if (result.annotation) setAnnotation(result.annotation);
      showToast("Task reopened for editing", "success");
      await loadQueue();
    });

  async function toggleAutoSubmit(enabled: boolean) {
    if (enabled && !window.confirm("Turn on auto-submit? Automation will submit tasks to RCR without review, starting from the next task.")) return;
    try {
      const result = await api.setAutoSubmit(enabled);
      setSettings((current) => (current ? { ...current, autoSubmitEnabled: result.autoSubmitEnabled } : current));
      showToast(enabled ? "Auto-submit ON" : "Auto-submit OFF (draft only)", enabled ? "info" : "success");
    } catch (error) {
      showToast(errorText(error), "error");
    }
  }

  const startAutomation = () =>
    run("Starting automation", async () => {
      const next = await api.startAutomation(mode, {
        limit: mode === "fixed_limit" ? limit : undefined,
        caseType: mode === "one_case" ? automationCase : undefined,
        sampleId: mode === "single_task" ? selected ?? undefined : undefined
      });
      setStatus(next);
    });

  const stopAutomation = () => run("Stopping", async () => setStatus(await api.stopAutomation()));

  return (
    <main>
      <header className="topbar">
        <h1>AutoTag RCR</h1>
        <div className="meta">
          <span>{settings?.model ?? "…"}</span>
          <label className={`toggle ${settings?.autoSubmitEnabled ? "on" : ""}`} title="When on, automation submits to RCR instead of only saving drafts. Applies from the next task.">
            <input type="checkbox" checked={!!settings?.autoSubmitEnabled} disabled={!settings} onChange={(event) => void toggleAutoSubmit(event.target.checked)} />
            Auto-submit
          </label>
          {settings && !settings.apiKeyConfigured ? <span className="chip bad">No model API key</span> : null}
          <span>{submittedCount} / {queue.length} submitted</span>
        </div>
      </header>

      <div className="layout">
        <aside className="rail">
          <section className="panel">
            <h2><Bot size={18} /> Automation</h2>
            <select value={mode} onChange={(event) => setMode(event.target.value as AutomationMode)} aria-label="Automation mode">
              <option value="all_open">All open tasks</option>
              <option value="one_case">One case type</option>
              <option value="fixed_limit">Fixed limit</option>
              <option value="single_task" disabled={!selected}>Selected task only</option>
            </select>
            {mode === "one_case" ? (
              <select value={automationCase} onChange={(event) => setAutomationCase(event.target.value as CaseType)} aria-label="Case type">
                {CASE_TYPES.map((item) => <option key={item}>{item}</option>)}
              </select>
            ) : null}
            {mode === "fixed_limit" ? (
              <input type="number" min={1} value={limit} aria-label="Task limit" onChange={(event) => setLimit(Math.max(1, Number(event.target.value) || 1))} />
            ) : null}
            <div className="row">
              <button disabled={status?.running} onClick={startAutomation}><Bot size={16} />Start</button>
              <button disabled={!status?.running} onClick={stopAutomation}><Square size={16} />Stop</button>
              <button onClick={() => { void refreshStatus(); void loadQueue(); }} aria-label="Refresh"><RefreshCw size={16} /></button>
            </div>
            <div className="status">
              <span>{status?.message ?? "Idle"}</span>
              <span>
                {status?.processed ?? 0}/{status?.total ?? 0} · drafted {status?.drafted ?? 0} · submitted {status?.submitted ?? 0} · review {status?.needs_review ?? 0} · failed {status?.failed ?? 0}
              </span>
              {status?.next_delay_seconds ? <span>Next in {status.next_delay_seconds}s</span> : null}
            </div>
          </section>

          <section className="panel queue">
            <h2>Queue</h2>
            <div className="tabs">
              {(["pending", "review", "drafted", "submitted", "all"] as QueueFilter[]).map((item) => (
                <button key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}>{item}</button>
              ))}
            </div>
            <select value={caseFilter} onChange={(event) => setCaseFilter(event.target.value as "all" | CaseType)} aria-label="Case filter">
              <option value="all">All cases</option>
              {CASE_TYPES.map((item) => <option key={item}>{item}</option>)}
            </select>
            <div className="queue-list">
              {visibleQueue.slice(0, 300).map((item) => (
                <button
                  key={item.sample_id}
                  className={`queue-item ${item.sample_id === selected ? "active" : ""}`}
                  onClick={() => void selectTask(item.sample_id)}
                >
                  <span className={`dot ${queueState(item)}`} />
                  <span className="queue-text">
                    <strong title={item.sample_id}>{shortId(item.sample_id)}</strong>
                    <small>{item.case_type} · {STATUS_LABEL[queueState(item)] ?? "To do"}</small>
                  </span>
                </button>
              ))}
              {!visibleQueue.length ? <p className="empty">No tasks match.</p> : null}
            </div>
          </section>
        </aside>

        <section className="content">
          {task && annotation ? (
            <>
              <div className="images">
                <ImagePane title="Query" side="query" task={task} image={task.query} annotation={annotation} disabled={submitted} onPick={(id) => edit((current) => toggleIdentity(current, id, activeSubject))} />
                <ImagePane title="Target" side="target" task={task} image={task.target} annotation={annotation} disabled={submitted} onPick={(id) => edit((current) => toggleIdentity(current, id, activeSubject))} />
              </div>

              <section className="panel editor">
                <div className="editor-head">
                  <div className="segmented" role="group" aria-label="Case">
                    {(["one", "DUAL", "RELATIONAL"] as const).map((item) => {
                      const active = item === "one" ? !isTwoSubject(annotation.case_type) : annotation.case_type === item;
                      return (
                        <button
                          key={item}
                          className={active ? "active" : ""}
                          disabled={submitted}
                          onClick={() => { edit((current) => setCaseMode(current, item)); if (item === "one") setActiveSubject(1); }}
                        >
                          {item === "one" ? (annotation.case_type === "GROUP" ? "GROUP" : "INDIVIDUAL") : item}
                        </button>
                      );
                    })}
                  </div>
                  <span className={`pill ${task.status}`}>{task.status}</span>
                  {localStatus ? <span className="pill">{STATUS_LABEL[localStatus] ?? localStatus}</span> : null}
                  <div className="editor-actions">
                    <button disabled={!!busy || submitted} onClick={generate}>
                      {busy === "Generating with AI" ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />}Generate
                    </button>
                    <button disabled={!!busy || submitted} onClick={() => void push(false)}><Save size={16} />Save draft</button>
                    {submitted ? (
                      <button disabled={!!busy} onClick={reopen}><Undo2 size={16} />Reopen</button>
                    ) : (
                      <button className="primary" disabled={!!busy || issues.length > 0} onClick={() => void push(true)}><Send size={16} />Submit &amp; next</button>
                    )}
                  </div>
                </div>

                <input className="notes" placeholder="Optional note for the AI, e.g. Subject 1 hands the diploma to Subject 2" value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={500} />

                <div className="subject-picker">
                  {annotation.subjects.map((subject) => (
                    <button
                      key={subject.subject_id}
                      className={`subject s${subject.subject_id} ${activeSubject === subject.subject_id ? "active" : ""}`}
                      disabled={submitted}
                      onClick={() => setActiveSubject(subject.subject_id)}
                    >
                      Subject {subject.subject_id} · {subject.identity_ids.length} identity
                    </button>
                  ))}
                  <small>Pick a subject, then click a box on either image.</small>
                </div>

                {annotation.subjects.map((subject, index) => (
                  <label key={subject.subject_id}>
                    Subject {subject.subject_id} · Identify as
                    <textarea
                      rows={2}
                      value={annotation.select_texts[index] ?? ""}
                      readOnly={submitted}
                      placeholder="the man in a dark suit standing on the left"
                      onChange={(event) =>
                        edit((current) => ({
                          ...current,
                          select_texts: current.subjects.map((_, i) => (i === index ? event.target.value : current.select_texts[i] ?? ""))
                        }))
                      }
                    />
                  </label>
                ))}
                <label>
                  Target condition
                  <textarea
                    rows={3}
                    value={annotation.target_condition}
                    readOnly={submitted}
                    placeholder={placeholderFor(annotation.case_type)}
                    onChange={(event) => edit((current) => ({ ...current, target_condition: event.target.value }))}
                  />
                </label>

                <div className="preview">
                  <small>Final instruction</small>
                  <p>{instruction}</p>
                </div>
                {issues.length ? (
                  <ul className="issues">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
                ) : null}
                {concerns.length ? (
                  <ul className="issues concerns">{concerns.map((note) => <li key={note}>{note}</li>)}</ul>
                ) : null}
              </section>
            </>
          ) : (
            <section className="panel empty-state">
              {busy ? <Loader2 className="spin" /> : null}
              <p>{busy ?? "Select a task from the queue."}</p>
            </section>
          )}
        </section>
      </div>
      {toast ? <div className={`toast ${toast.type}`}>{toast.message}</div> : null}
    </main>
  );
}

function placeholderFor(caseType: CaseType): string {
  if (caseType === "RELATIONAL") return "Subject 1 is presenting a diploma to Subject 2";
  if (caseType === "DUAL") return "Subject 1 is holding a diploma and Subject 2 is clapping";
  if (caseType === "GROUP") return "The members of Subject 1 are standing together on the stage";
  return "Subject 1 is holding a diploma";
}

function ImagePane({
  title,
  side,
  task,
  image,
  annotation,
  disabled,
  onPick
}: {
  title: string;
  side: "query" | "target";
  task: Task;
  image: TaskImage;
  annotation: RcrAnnotation;
  disabled: boolean;
  onPick: (identityId: string) => void;
}) {
  const assignment = assignmentOf(annotation);
  return (
    <section className="panel image-pane">
      <h2>{title}</h2>
      <div className="frame">
        <img src={imageUrl(task.sample_id, side)} alt={`${title} image`} />
        {image.boxes.map((box) => {
          const subject = assignment.get(box.identity_id);
          const selectable = task.candidate_identity_ids.includes(box.identity_id);
          return (
            <button
              key={box.identity_id}
              className={`box ${subject ? `s${subject}` : ""}`}
              disabled={disabled || !selectable}
              title={`Identity ${box.identity_id}${subject ? ` → Subject ${subject}` : ""}`}
              style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}
              onClick={() => onPick(box.identity_id)}
            >
              <span>{subject ? `S${subject} · ` : ""}{box.identity_id}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
