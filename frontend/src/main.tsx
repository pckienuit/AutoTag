import { Bot, Loader2, RefreshCw, Save, Send, Sparkles, Square } from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import "./styles.css";
import type {
  AutomationMode,
  AutomationStatus,
  Box,
  CaseType,
  ReviewTask,
  Stage2Annotation,
  SubjectAnnotation,
  Task,
  TaskImage
} from "./types";

const emptySubject = (subjectId: number): SubjectAnnotation => ({
  subjectId,
  targetConstraintEnabled: true,
  queryGroupIds: [],
  targetGroupIds: [],
  descQueryRaw: "",
  descQueryFinal: "",
  changeTargetRaw: "",
  changeTargetFinal: ""
});

const emptyAnnotation = (): Stage2Annotation => ({
  schemaVersion: "1.0",
  caseType: "SINGLE",
  targetConstraintEnabled: true,
  relationalSubject1ChangeEnabled: false,
  relationalSubject2ChangeEnabled: false,
  captionRaw: null,
  captionFinal: null,
  pairChangeRaw: null,
  pairChangeFinal: null,
  llmEdits: [],
  subjects: [emptySubject(1)]
});

function imageBySide(task: Task | null, side: "QUERY" | "TARGET"): TaskImage | null {
  const wanted = side === "QUERY" ? new Set(["QUERY", "A"]) : new Set(["TARGET", "B"]);
  return task?.images?.find((image) => wanted.has(String(image.side).toUpperCase())) ?? null;
}

function fullImageUrl(url?: string): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("http")) return url;
  return `https://aiclub.uit.edu.vn${url}`;
}

function cleanFragment(value: string | null | undefined): string {
  let text = String(value || "").trim();
  text = text.replace(/;/g, ",").split(/\s+/).join(" ");
  return text.replace(/^[ ,.!?]+|[ ,.!?]+$/g, "");
}

function normalizeChange(value: string | null | undefined): string {
  let text = cleanFragment(value);
  const lowered = text.toLowerCase();
  for (const prefix of ["subject 1 ", "subject 2 "]) {
    if (lowered.startsWith(prefix)) {
      text = text.substring(prefix.length).replace(/^[ :,\-]+|[ :,\-]+$/g, "");
      break;
    }
  }
  return text;
}

function normalizePair(value: string | null | undefined): string {
  const text = cleanFragment(value);
  if (!text) return "";
  const lowered = text.toLowerCase();
  if (lowered.startsWith("subject 1 ") && lowered.includes(" subject 2")) {
    return text;
  }
  return `Subject 1 ${text} Subject 2`;
}

function buildCaption(annotation: Stage2Annotation): string {
  const subjects = [...annotation.subjects].sort((a, b) => a.subjectId - b.subjectId);
  const s1 = subjects.find((subject) => subject.subjectId === 1);
  const s2 = subjects.find((subject) => subject.subjectId === 2);
  if (!s1) return "";

  const desc1 = cleanFragment(s1.descQueryFinal || s1.descQueryRaw);
  const change1 = normalizeChange(s1.changeTargetFinal || s1.changeTargetRaw);

  if (annotation.caseType === "SINGLE") {
    if (!desc1 || !change1) return "";
    return `In the query image, Subject 1 refers to ${desc1}. Retrieve target images where Subject 1 ${change1}.`;
  }

  if (!s2) return "";
  const desc2 = cleanFragment(s2.descQueryFinal || s2.descQueryRaw);
  const change2 = normalizeChange(s2.changeTargetFinal || s2.changeTargetRaw);

  if (annotation.caseType === "MULTI") {
    if (!desc1 || !desc2 || !change1 || !change2) return "";
    return `In the query image, Subject 1 refers to ${desc1}, and Subject 2 refers to ${desc2}. Retrieve target images where Subject 1 ${change1} and Subject 2 ${change2}.`;
  }

  const pair = normalizePair(annotation.pairChangeFinal || annotation.pairChangeRaw);
  if (!desc1 || !desc2 || !pair) return "";

  const extras: string[] = [];
  if (annotation.relationalSubject1ChangeEnabled && change1) {
    extras.push(`Subject 1 ${change1}`);
  }
  if (annotation.relationalSubject2ChangeEnabled && change2) {
    extras.push(`Subject 2 ${change2}`);
  }

  let suffix = "";
  if (extras.length === 1) {
    suffix = `, with ${extras[0]}`;
  } else if (extras.length === 2) {
    suffix = `, with ${extras[0]} and ${extras[1]}`;
  }

  return `In the query image, Subject 1 refers to ${desc1}, and Subject 2 refers to ${desc2}. Retrieve target images where ${pair}${suffix}.`;
}

function App() {
  const [sessionId, setSessionId] = useState("");
  const [task, setTask] = useState<Task | null>(null);
  const [activeTaskMode, setActiveTaskMode] = useState<"manual" | "review">("manual");
  const [annotation, setAnnotation] = useState<Stage2Annotation>(emptyAnnotation());
  const [notes, setNotes] = useState("");
  const [issues, setIssues] = useState<string[]>([]);
  const [automationMode, setAutomationMode] = useState<AutomationMode>("all_open");
  const [automationLimit, setAutomationLimit] = useState(10);
  const [automationStatus, setAutomationStatus] = useState<AutomationStatus | null>(null);
  const [reviewTasks, setReviewTasks] = useState<ReviewTask[]>([]);
  const [reviewFilter, setReviewFilter] = useState("all");
  const [message, setMessage] = useState("Ready");
  const [busy, setBusy] = useState(false);
  const [activeSubjectId, setActiveSubjectId] = useState<number>(1);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  const caption = useMemo(() => buildCaption(annotation), [annotation]);

  useEffect(() => {
    void loadAutoTask();
    void refreshAutomationStatus();
    void loadReviewTasks();
  }, []);

  useEffect(() => {
    if (!automationStatus?.running) return;
    const timer = setInterval(() => {
      void refreshAutomationStatus();
      void loadReviewTasks();
    }, 5000);
    return () => clearInterval(timer);
  }, [automationStatus?.running]);

  function showToast(msg: string, type: "success" | "error" | "info" = "info") {
    setToast({ message: msg, type });
    setTimeout(() => {
      setToast((curr) => curr?.message === msg ? null : curr);
    }, 4000);
  }

  function updateSubject(subjectId: number, patch: Partial<SubjectAnnotation>) {
    setAnnotation((current) => ({
      ...current,
      subjects: current.subjects.map((subject) =>
        subject.subjectId === subjectId ? { ...subject, ...patch } : subject
      )
    }));
  }

  function handleBoxPick(subjectId: number, type: "QUERY" | "TARGET", boxId: string) {
    setAnnotation((current) => ({
      ...current,
      subjects: current.subjects.map((subject) => {
        if (subject.subjectId !== subjectId) return subject;
        const field = type === "QUERY" ? "queryGroupIds" : "targetGroupIds";
        const currentIds = subject[field] || [];
        const nextIds = currentIds.includes(boxId)
          ? currentIds.filter((id) => id !== boxId)
          : [...currentIds, boxId];
        return { ...subject, [field]: nextIds };
      })
    }));
  }

  function setCaseType(caseType: CaseType) {
    setAnnotation((current) => {
      const subjects =
        caseType === "SINGLE"
          ? [current.subjects.find((item) => item.subjectId === 1) ?? emptySubject(1)]
          : [
              current.subjects.find((item) => item.subjectId === 1) ?? emptySubject(1),
              current.subjects.find((item) => item.subjectId === 2) ?? emptySubject(2)
            ];
      return {
        ...current,
        caseType,
        subjects,
        relationalSubject1ChangeEnabled: false,
        relationalSubject2ChangeEnabled: false
      };
    });
    if (caseType === "SINGLE") {
      setActiveSubjectId(1);
    }
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(true);
    setMessage(label);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      showToast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      setBusy(false);
    }
  }

  async function loadAutoTask() {
    await run("Loading next task", async () => {
      const data = await api.autoCurrentTask();
      setSessionId(data.sessionId ?? "");
      setTask(data.task);
      setActiveTaskMode("manual");
      setAnnotation(emptyAnnotation());
      setIssues([]);
      setMessage(data.task ? "Task loaded" : "No incomplete task found");
      showToast(data.task ? "Task loaded automatically" : "No incomplete task found", data.task ? "success" : "info");
    });
  }

  async function refreshAutomationStatus() {
    const status = await api.automationStatus();
    setAutomationStatus(status);
  }

  async function loadReviewTasks() {
    const data = await api.reviewTasks();
    setReviewTasks(data.tasks);
  }

  async function startAutomation() {
    await run("Starting automation", async () => {
      const limit = automationMode === "fixed_limit" ? automationLimit : null;
      const status = await api.startAutomation(automationMode, limit);
      setAutomationStatus(status);
      setMessage("Automation started");
      showToast("Automation started", "success");
    });
  }

  async function stopAutomation() {
    await run("Stopping automation", async () => {
      const status = await api.stopAutomation();
      setAutomationStatus(status);
      await loadReviewTasks();
      showToast("Automation stop requested", "info");
    });
  }

  async function openReviewTask(item: ReviewTask) {
    await run(`Loading review ${item.taskId}`, async () => {
      let reviewTask = item.task;
      if (!reviewTask.images?.length) {
        const data = await api.task(item.sessionId, item.taskId);
        reviewTask = data.task;
      }
      setSessionId(item.sessionId);
      setTask(reviewTask);
      setActiveTaskMode("review");
      setAnnotation(item.annotation ?? emptyAnnotation());
      setIssues(item.issues ?? []);
      setMessage(`Reviewing ${item.taskId}`);
    });
  }

  const visibleReviewTasks = reviewTasks.filter((item) => reviewFilter === "all" || item.status === reviewFilter);
  const isReviewTask = activeTaskMode === "review";
  const isManualTask = activeTaskMode === "manual";

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>AutoTag CPR</h1>
          <p>AI draft, fast review, controlled sync to UIT.</p>
        </div>
        <div className="status">{busy ? <Loader2 className="spin" size={16} /> : null}{message}</div>
      </header>

      {toast && (
        <div className={`toast toast-${toast.type}`} onClick={() => setToast(null)}>
          {toast.message}
        </div>
      )}

      <section className="rail">
        <div className="panel compact">
          <h2><RefreshCw size={18} /> Auto task</h2>
          <button onClick={loadAutoTask}><RefreshCw size={16} />Reload task</button>
        </div>
        <div className="panel compact">
          <h2><Bot size={18} /> Automation</h2>
          <select value={automationMode} onChange={(event) => setAutomationMode(event.target.value as AutomationMode)}>
            <option value="all_open">All open sessions</option>
            <option value="one_session">One session</option>
            <option value="fixed_limit">Fixed limit</option>
            <option value="current_task">Current task only</option>
          </select>
          {automationMode === "fixed_limit" ? (
            <input type="number" min={1} value={automationLimit} onChange={(event) => setAutomationLimit(Math.max(1, Number(event.target.value) || 1))} />
          ) : null}
          <div className="automation-actions">
            <button disabled={automationStatus?.running} onClick={startAutomation}><Bot size={16} />Start</button>
            <button disabled={!automationStatus?.running} onClick={stopAutomation}><Square size={16} />Stop</button>
            <button onClick={() => { void refreshAutomationStatus(); void loadReviewTasks(); }}><RefreshCw size={16} />Refresh</button>
          </div>
          <div className="automation-status">
            <span>{automationStatus?.message ?? "Idle"}</span>
            <span>Done {automationStatus?.processed ?? 0} / Submitted {automationStatus?.submitted ?? 0} / Failed {automationStatus?.failed ?? 0} / Review {automationStatus?.needs_review ?? 0}</span>
            {automationStatus?.next_delay_seconds ? <span>Next in {automationStatus.next_delay_seconds}s</span> : null}
          </div>
        </div>
      </section>

      <section className="workspace">
        <ImagePane
          title="Query image"
          type="QUERY"
          image={imageBySide(task, "QUERY")}
          annotation={annotation}
          activeSubjectId={activeSubjectId}
          onPick={(id) => handleBoxPick(activeSubjectId, "QUERY", id)}
        />
        <ImagePane
          title="Target image"
          type="TARGET"
          image={imageBySide(task, "TARGET")}
          annotation={annotation}
          activeSubjectId={activeSubjectId}
          onPick={(id) => handleBoxPick(activeSubjectId, "TARGET", id)}
        />
      </section>

      <section className="editor panel">
        <div className="editor-head">
          <div className="segmented">
            {(["SINGLE", "MULTI", "RELATIONAL"] as CaseType[]).map((item) => (
              <button className={annotation.caseType === item ? "active" : ""} onClick={() => setCaseType(item)} key={item}>{item}</button>
            ))}
          </div>
          <div className="editor-actions">
            <button disabled={!task} onClick={() => run("Generating with AI", async () => { if (!task) return; const data = await api.generate(task, notes); setAnnotation(data.annotation); setIssues(data.issues); setMessage("AI draft ready"); showToast("AI Draft annotation generated", "success"); })}><Sparkles size={16} />Generate</button>
            <button disabled={!task || !isReviewTask} onClick={() => run("Saving to UIT", async () => { if (!task) return; const result = await api.save(sessionId, task, { ...annotation, captionFinal: caption }); if (result.task) setTask(result.task); setIssues(result.issues ?? []); setMessage(result.status); if (result.status === "saved") { showToast("Draft saved successfully to UIT" + (result.issues?.length ? " with warnings" : ""), result.issues?.length ? "info" : "success"); } else { showToast("Save failed", "error"); } })}><Save size={16} />Sync save</button>
            <button disabled={!task || !isManualTask} onClick={() => run("Submitting", async () => { if (!task) return; const result = await api.submit(sessionId, task, { ...annotation, captionFinal: caption }); setIssues(result.issues ?? []); setMessage(result.status); if (result.status === "submitted") { showToast("Annotation submitted successfully!", "success"); const next = await api.autoCurrentTask(); setSessionId(next.sessionId ?? ""); setTask(next.task); setActiveTaskMode("manual"); setAnnotation(emptyAnnotation()); } else { showToast("Submission blocked: please fix issues", "error"); } })}><Send size={16} />Submit & next</button>
          </div>
        </div>

        <textarea className="notes" placeholder="Optional guidance for AI, e.g. focus on the child in red shirt" value={notes} onChange={(event) => setNotes(event.target.value)} />

        <div className="subjects">
          {annotation.subjects.map((subject) => {
            const isActive = subject.subjectId === activeSubjectId;
            return (
              <div
                className={`subject ${isActive ? "active" : ""}`}
                onClick={() => setActiveSubjectId(subject.subjectId)}
                key={subject.subjectId}
                style={{ cursor: "pointer" }}
              >
                <div className="subject-header">
                  <strong>Subject {subject.subjectId}</strong>
                  {isActive && <span className="active-badge">Active Box Editor</span>}
                </div>
                <div onClick={(e) => e.stopPropagation()} style={{ display: "grid", gap: "8px", marginTop: "8px" }}>
                  <div>
                    <label style={{ fontSize: "12px", color: "#666" }}>Query group IDs (Click boxes in Query Image)</label>
                    <input placeholder="e.g. 193" value={subject.queryGroupIds.join(", ")} onChange={(event) => updateSubject(subject.subjectId, { queryGroupIds: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} />
                  </div>
                  <div>
                    <label style={{ fontSize: "12px", color: "#666" }}>Target group IDs (Click boxes in Target Image)</label>
                    <input placeholder="e.g. 193" value={subject.targetGroupIds.join(", ")} onChange={(event) => updateSubject(subject.subjectId, { targetGroupIds: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} />
                  </div>
                  <textarea placeholder="DESC in query image" value={subject.descQueryFinal} onChange={(event) => updateSubject(subject.subjectId, { descQueryRaw: event.target.value, descQueryFinal: event.target.value })} />
                  {annotation.caseType !== "RELATIONAL" || subject.subjectId === 1 && annotation.relationalSubject1ChangeEnabled || subject.subjectId === 2 && annotation.relationalSubject2ChangeEnabled ? (
                    <textarea placeholder="CHANGE in target image" value={subject.changeTargetFinal} onChange={(event) => updateSubject(subject.subjectId, { changeTargetRaw: event.target.value, changeTargetFinal: event.target.value })} />
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        {annotation.caseType === "RELATIONAL" ? (
          <div className="relation">
            <input placeholder="PAIR_CHANGE, e.g. is standing behind" value={annotation.pairChangeFinal ?? ""} onChange={(event) => setAnnotation({ ...annotation, pairChangeRaw: event.target.value, pairChangeFinal: event.target.value })} />
            <label><input type="checkbox" checked={annotation.relationalSubject1ChangeEnabled} onChange={(event) => setAnnotation({ ...annotation, relationalSubject1ChangeEnabled: event.target.checked })} /> Subject 1 extra CHANGE</label>
            <label><input type="checkbox" checked={annotation.relationalSubject2ChangeEnabled} onChange={(event) => setAnnotation({ ...annotation, relationalSubject2ChangeEnabled: event.target.checked })} /> Subject 2 extra CHANGE</label>
          </div>
        ) : null}

        <div className="caption">
          <span>Final caption</span>
          <p>{caption || "Fill required fields to preview the final caption."}</p>
        </div>
        {issues.length ? <ul className="issues">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
      </section>

      <section className="review-panel panel">
        <div className="review-head">
          <h2>Review queue</h2>
          <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}>
            <option value="all">All</option>
            <option value="submitted">Submitted</option>
            <option value="needs_review">Needs review</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="review-list">
          {visibleReviewTasks.map((item) => (
            <button className="review-item" onClick={() => { void openReviewTask(item); }} key={item.taskId}>
              <strong>{item.status}</strong>
              <span>{item.taskId}</span>
              <small>{item.updatedAt}</small>
              <a href={item.workUrl} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Open UIT task</a>
              <a href={item.submissionsUrl} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Submissions</a>
            </button>
          ))}
          {!visibleReviewTasks.length ? <div className="empty review-empty">No review items yet</div> : null}
        </div>
      </section>
    </main>
  );
}

function ImagePane({
  title,
  type,
  image,
  annotation,
  activeSubjectId,
  onPick
}: {
  title: string;
  type: "QUERY" | "TARGET";
  image: TaskImage | null;
  annotation: Stage2Annotation;
  activeSubjectId: number;
  onPick: (id: string) => void;
}) {
  const boxes = image?.boxes ?? [];
  const activeSubject = annotation.subjects.find((s) => s.subjectId === activeSubjectId);
  const activeIds = new Set(type === "QUERY" ? activeSubject?.queryGroupIds : activeSubject?.targetGroupIds);

  const otherSubjects = annotation.subjects.filter((s) => s.subjectId !== activeSubjectId);
  const otherIds = new Set(otherSubjects.flatMap((s) => type === "QUERY" ? s.queryGroupIds : s.targetGroupIds));

  return (
    <div className="panel image-panel">
      <h2>{title}</h2>
      {image?.imageUrl ? (
        <div style={{ position: "relative", display: "inline-block", maxWidth: "100%", margin: "0 auto" }}>
          <img
            src={fullImageUrl(image.imageUrl)}
            alt={title}
            style={{
              display: "block",
              maxWidth: "100%",
              maxHeight: "420px",
              width: "auto",
              height: "auto",
              borderRadius: "12px",
              border: "1px solid var(--border-color)",
              background: "#171d2c",
            }}
          />
          {boxes.map((box: Box) => {
            const id = String(box.groupUid || box.label || box.id);
            const isActive = activeIds.has(id);
            const isOther = otherIds.has(id);

            const boxStyle: React.CSSProperties = {
              position: "absolute",
              left: `${(box.x ?? 0) * 100}%`,
              top: `${(box.y ?? 0) * 100}%`,
              width: `${(box.width ?? 0) * 100}%`,
              height: `${(box.height ?? 0) * 100}%`,
              border: isActive
                ? "2px solid #14b8a6"
                : isOther
                ? "2px dashed #f59e0b"
                : "1px solid rgba(255, 255, 255, 0.5)",
              boxShadow: isActive ? "0 0 8px rgba(20, 184, 166, 0.6)" : "none",
              backgroundColor: isActive
                ? "rgba(20, 184, 166, 0.15)"
                : isOther
                ? "rgba(245, 158, 11, 0.05)"
                : "rgba(255, 255, 255, 0.05)",
              cursor: "pointer",
              boxSizing: "border-box",
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "flex-start",
              transition: "all 0.15s ease",
            };

            const labelStyle: React.CSSProperties = {
              backgroundColor: isActive
                ? "#14b8a6"
                : isOther
                ? "#f59e0b"
                : "rgba(0, 0, 0, 0.6)",
              color: isActive ? "#0c0e12" : "#ffffff",
              fontSize: "10px",
              fontWeight: "bold",
              padding: "1px 4px",
              borderRadius: "0 0 4px 0",
              pointerEvents: "none",
              userSelect: "none",
              lineHeight: "1.2",
            };

            return (
              <div
                key={String(box.id)}
                style={boxStyle}
                onClick={() => onPick(id)}
                title={`Box ${id}`}
              >
                <span style={labelStyle}>{id}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="empty">No image loaded</div>
      )}
      <div className="box-list">
        {boxes.map((box: Box) => {
          const id = String(box.groupUid || box.label || box.id);
          const isActive = activeIds.has(id);
          const isOther = otherIds.has(id);
          let className = "";
          if (isActive) className = "picked";
          else if (isOther) className = "picked-other";

          return <button className={className} onClick={() => onPick(id)} key={String(box.id)}>{id}</button>;
        })}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
