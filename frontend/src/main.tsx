import { Bot, Loader2, RefreshCw, Save, Send, Sparkles, Square } from "lucide-react";
import React, { useEffect, useMemo, useRef, useState } from "react";
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

function mergeReviewTasks(current: ReviewTask[], incoming: ReviewTask[]): ReviewTask[] {
  const currentById = new Map(current.map((item) => [item.taskId, item]));
  return incoming.map((item) => {
    const existing = currentById.get(item.taskId);
    if (!existing) return item;
    const task = item.task.images?.length || !existing.task.images?.length
      ? item.task
      : { ...item.task, images: existing.task.images };
    return {
      ...item,
      task,
      annotation: item.annotation ?? existing.annotation,
      caption: item.caption || existing.caption
    };
  });
}

function syncGroupIds(ids: string[]): Pick<SubjectAnnotation, "queryGroupIds" | "targetGroupIds"> {
  const uniqueIds = [...new Set(ids)];
  return { queryGroupIds: uniqueIds, targetGroupIds: uniqueIds };
}

function toggleSyncedGroupId(subject: SubjectAnnotation, boxId: string): Pick<SubjectAnnotation, "queryGroupIds" | "targetGroupIds"> {
  const currentIds = new Set([...subject.queryGroupIds, ...subject.targetGroupIds]);
  if (currentIds.has(boxId)) {
    currentIds.delete(boxId);
  } else {
    currentIds.add(boxId);
  }
  return syncGroupIds([...currentIds]);
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
  const [reviewDrafts, setReviewDrafts] = useState<Record<string, Stage2Annotation>>({});
  const [reviewFilter, setReviewFilter] = useState("all");
  const [message, setMessage] = useState("Ready");
  const [busy, setBusy] = useState(false);
  const [activeSubjectId, setActiveSubjectId] = useState<number>(1);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);
  const dirtyReviewDraftIds = useRef(new Set<string>());
  const hydratingReviewTaskIds = useRef(new Set<string>());

  const caption = useMemo(() => buildCaption(annotation), [annotation]);

  useEffect(() => {
    void loadAutoTask();
    void refreshAutomationStatus();
    void loadReviewTasks();
  }, []);

  useEffect(() => {
    if (!automationStatus?.running) return;
    const statusTimer = setInterval(() => {
      void refreshAutomationStatus();
    }, automationStatus.next_delay_seconds ? 1000 : 5000);
    const reviewTimer = setInterval(() => {
      void loadReviewTasks();
    }, 5000);
    return () => {
      clearInterval(statusTimer);
      clearInterval(reviewTimer);
    };
  }, [automationStatus?.running, automationStatus?.next_delay_seconds]);

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

  function handleBoxPick(subjectId: number, _type: "QUERY" | "TARGET", boxId: string) {
    setAnnotation((current) => ({
      ...current,
      subjects: current.subjects.map((subject) => {
        if (subject.subjectId !== subjectId) return subject;
        return { ...subject, ...toggleSyncedGroupId(subject, boxId) };
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
    setReviewTasks((current) => mergeReviewTasks(current, data.tasks));
    void hydrateReviewTaskImages(data.tasks);
    setReviewDrafts((current) => {
      const next = { ...current };
      for (const item of data.tasks) {
        const incoming = item.annotation;
        if (!next[item.taskId]) {
          next[item.taskId] = item.annotation ?? emptyAnnotation();
        } else if (
          incoming &&
          !dirtyReviewDraftIds.current.has(item.taskId) &&
          !buildCaption(next[item.taskId]) &&
          buildCaption(incoming)
        ) {
          next[item.taskId] = incoming;
        }
      }
      return next;
    });
  }

  async function hydrateReviewTaskImages(items: ReviewTask[]) {
    const missingImageItems = items.filter((item) => !item.task.images?.length && !hydratingReviewTaskIds.current.has(item.taskId));
    if (!missingImageItems.length) return;
    for (const item of missingImageItems) {
      hydratingReviewTaskIds.current.add(item.taskId);
    }
    const hydratedItems = await Promise.all(
      missingImageItems.map(async (item) => {
        try {
          const data = await api.task(item.sessionId, item.taskId);
          return { taskId: item.taskId, task: data.task };
        } catch {
          return null;
        }
      })
    );
    for (const item of missingImageItems) {
      hydratingReviewTaskIds.current.delete(item.taskId);
    }
    setReviewTasks((current) =>
      current.map((item) => {
        const hydrated = hydratedItems.find((result) => result?.taskId === item.taskId);
        return hydrated ? { ...item, task: hydrated.task } : item;
      })
    );
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

  async function fixManualText(value: string, field: string, apply: (text: string) => void) {
    await run(`Fixing ${field} with AI`, async () => {
      const data = await api.fixText(value, field);
      apply(data.text);
      showToast("Text fixed with AI", "success");
    });
  }

  function reviewDraftFor(item: ReviewTask): Stage2Annotation {
    return reviewDrafts[item.taskId] ?? item.annotation ?? emptyAnnotation();
  }

  function updateReviewDraft(taskId: string, updater: (current: Stage2Annotation) => Stage2Annotation) {
    dirtyReviewDraftIds.current.add(taskId);
    setReviewDrafts((current) => ({
      ...current,
      [taskId]: updater(current[taskId] ?? emptyAnnotation())
    }));
  }

  async function approveReviewTask(item: ReviewTask) {
    await run(`Approving ${item.taskId}`, async () => {
      const draft = reviewDraftFor(item);
      const annotationToSave = { ...draft, captionFinal: buildCaption(draft) };
      const hasLocalEdits = dirtyReviewDraftIds.current.has(item.taskId);
      const result = hasLocalEdits
        ? await api.save(item.sessionId, item.task, annotationToSave, 0, true)
        : await api.approveReview(item.sessionId, item.task, annotationToSave, item.issues);
      dirtyReviewDraftIds.current.delete(item.taskId);
      setReviewDrafts((current) => ({ ...current, [item.taskId]: annotationToSave }));
      setReviewTasks((current) =>
        current.map((reviewItem) =>
          reviewItem.taskId === item.taskId
            ? {
                ...reviewItem,
                annotation: annotationToSave,
                caption: annotationToSave.captionFinal ?? "",
                issues: result.issues ?? [],
                reviewed: true,
                status: "reviewed"
              }
            : reviewItem
        )
      );
      const warning = hasLocalEdits && "warnings" in result && Array.isArray(result.warnings) ? result.warnings[0] : undefined;
      showToast(warning ?? (hasLocalEdits ? `Synced and reviewed ${item.taskId}` : `Reviewed ${item.taskId}`), warning ? "info" : "success");
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
            <button disabled={!task || !isReviewTask} onClick={() => run("Saving to UIT", async () => { if (!task) return; const result = await api.save(sessionId, task, { ...annotation, captionFinal: caption }, 0, true); if (result.task) setTask(result.task); setIssues(result.issues ?? []); setMessage(result.status); await loadReviewTasks(); if (result.status === "saved" || result.status === "reviewed_local") { showToast(result.warnings?.[0] ?? "Draft saved successfully to UIT" + (result.issues?.length ? " with warnings" : ""), result.warnings?.length || result.issues?.length ? "info" : "success"); } else { showToast("Save failed", "error"); } })}><Save size={16} />Sync save</button>
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
                    <input placeholder="e.g. 193" value={subject.queryGroupIds.join(", ")} onChange={(event) => updateSubject(subject.subjectId, syncGroupIds(splitIds(event.target.value)))} />
                  </div>
                  <div>
                    <label style={{ fontSize: "12px", color: "#666" }}>Target group IDs (Click boxes in Target Image)</label>
                    <input placeholder="e.g. 193" value={subject.targetGroupIds.join(", ")} onChange={(event) => updateSubject(subject.subjectId, syncGroupIds(splitIds(event.target.value)))} />
                  </div>
                  <div className="fix-field">
                    <textarea placeholder="DESC in query image" value={subject.descQueryFinal} onChange={(event) => updateSubject(subject.subjectId, { descQueryRaw: event.target.value, descQueryFinal: event.target.value })} />
                    <button type="button" disabled={!subject.descQueryFinal.trim() || busy} onClick={() => { void fixManualText(subject.descQueryFinal, "DESC", (text) => updateSubject(subject.subjectId, { descQueryRaw: text, descQueryFinal: text })); }}><Sparkles size={14} />Fix with AI</button>
                  </div>
                  {annotation.caseType !== "RELATIONAL" || subject.subjectId === 1 && annotation.relationalSubject1ChangeEnabled || subject.subjectId === 2 && annotation.relationalSubject2ChangeEnabled ? (
                    <div className="fix-field">
                      <textarea placeholder="CHANGE in target image" value={subject.changeTargetFinal} onChange={(event) => updateSubject(subject.subjectId, { changeTargetRaw: event.target.value, changeTargetFinal: event.target.value })} />
                      <button type="button" disabled={!subject.changeTargetFinal.trim() || busy} onClick={() => { void fixManualText(subject.changeTargetFinal, "CHANGE", (text) => updateSubject(subject.subjectId, { changeTargetRaw: text, changeTargetFinal: text })); }}><Sparkles size={14} />Fix with AI</button>
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        {annotation.caseType === "RELATIONAL" ? (
          <div className="relation">
            <div className="fix-field">
              <input placeholder="PAIR_CHANGE, e.g. is standing behind" value={annotation.pairChangeFinal ?? ""} onChange={(event) => setAnnotation({ ...annotation, pairChangeRaw: event.target.value, pairChangeFinal: event.target.value })} />
              <button type="button" disabled={!(annotation.pairChangeFinal ?? "").trim() || busy} onClick={() => { void fixManualText(annotation.pairChangeFinal ?? "", "PAIR_CHANGE", (text) => setAnnotation({ ...annotation, pairChangeRaw: text, pairChangeFinal: text })); }}><Sparkles size={14} />Fix with AI</button>
            </div>
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
            <option value="not_reviewed">Not reviewed</option>
            <option value="reviewed">Reviewed</option>
            <option value="submitted">Submitted</option>
            <option value="needs_review">Needs review</option>
            <option value="failed">Failed</option>
          </select>
        </div>
        <div className="review-list">
          {visibleReviewTasks.map((item) => (
            <ReviewQuickCard
              item={item}
              draft={reviewDraftFor(item)}
              disabled={busy}
              onApprove={() => { void approveReviewTask(item); }}
              onOpen={() => { void openReviewTask(item); }}
              onChange={(updater) => updateReviewDraft(item.taskId, updater)}
              key={item.taskId}
            />
          ))}
          {!visibleReviewTasks.length ? <div className="empty review-empty">No review items yet</div> : null}
        </div>
      </section>
    </main>
  );
}

function ReviewQuickCard({
  item,
  draft,
  disabled,
  onApprove,
  onOpen,
  onChange
}: {
  item: ReviewTask;
  draft: Stage2Annotation;
  disabled: boolean;
  onApprove: () => void;
  onOpen: () => void;
  onChange: (updater: (current: Stage2Annotation) => Stage2Annotation) => void;
}) {
  const reviewed = item.reviewed || item.status === "reviewed";
  const captionPreview = buildCaption(draft) || item.caption;

  function setReviewCaseType(caseType: CaseType) {
    onChange((current) => {
      const subjects =
        caseType === "SINGLE"
          ? [current.subjects.find((subject) => subject.subjectId === 1) ?? emptySubject(1)]
          : [
              current.subjects.find((subject) => subject.subjectId === 1) ?? emptySubject(1),
              current.subjects.find((subject) => subject.subjectId === 2) ?? emptySubject(2)
            ];
      return {
        ...current,
        caseType,
        subjects,
        relationalSubject1ChangeEnabled: false,
        relationalSubject2ChangeEnabled: false
      };
    });
  }

  function updateReviewSubject(subjectId: number, patch: Partial<SubjectAnnotation>) {
    onChange((current) => ({
      ...current,
      subjects: current.subjects.map((subject) =>
        subject.subjectId === subjectId ? { ...subject, ...patch } : subject
      )
    }));
  }

  function toggleReviewBox(subjectId: number, _type: "QUERY" | "TARGET", boxId: string) {
    onChange((current) => ({
      ...current,
      subjects: current.subjects.map((subject) => {
        if (subject.subjectId !== subjectId) return subject;
        return { ...subject, ...toggleSyncedGroupId(subject, boxId) };
      })
    }));
  }

  return (
    <article className={`review-card ${reviewed ? "review-card-reviewed" : ""}`}>
      <div className="review-card-check">
        <label title={reviewed ? "Already reviewed" : "Approve and mark reviewed"}>
          <input
            type="checkbox"
            checked={reviewed}
            disabled={disabled || reviewed}
            onChange={(event) => {
              if (event.target.checked) onApprove();
            }}
          />
          <span>Approve</span>
        </label>
      </div>

      <div className="review-card-body">
        <div className="review-card-top">
          <div>
            <strong>{item.status}</strong>
            <span>{item.taskId}</span>
            <small>{item.updatedAt}</small>
          </div>
          <div className="review-card-links">
            <button type="button" onClick={onOpen}>Open editor</button>
            <a href={item.workUrl} target="_blank" rel="noreferrer">UIT task</a>
            <a href={item.submissionsUrl} target="_blank" rel="noreferrer">Submissions</a>
          </div>
        </div>

        <div className="review-card-grid">
          <ReviewThumb
            title="Query"
            type="QUERY"
            image={imageBySide(item.task, "QUERY")}
            annotation={draft}
            onToggleBox={(subjectId, boxId) => toggleReviewBox(subjectId, "QUERY", boxId)}
          />
          <ReviewThumb
            title="Target"
            type="TARGET"
            image={imageBySide(item.task, "TARGET")}
            annotation={draft}
            onToggleBox={(subjectId, boxId) => toggleReviewBox(subjectId, "TARGET", boxId)}
          />
        </div>

        <div className="quick-fields">
          <select value={draft.caseType} onChange={(event) => setReviewCaseType(event.target.value as CaseType)}>
            <option value="SINGLE">SINGLE</option>
            <option value="MULTI">MULTI</option>
            <option value="RELATIONAL">RELATIONAL</option>
          </select>
          {draft.subjects.map((subject) => (
            <div className="quick-subject" key={subject.subjectId}>
              <strong>Subject {subject.subjectId}</strong>
              <input
                placeholder="Query group IDs"
                value={subject.queryGroupIds.join(", ")}
                onChange={(event) => updateReviewSubject(subject.subjectId, syncGroupIds(splitIds(event.target.value)))}
              />
              <input
                placeholder="Target group IDs"
                value={subject.targetGroupIds.join(", ")}
                onChange={(event) => updateReviewSubject(subject.subjectId, syncGroupIds(splitIds(event.target.value)))}
              />
              <textarea
                placeholder="DESC in query image"
                value={subject.descQueryFinal}
                onChange={(event) => updateReviewSubject(subject.subjectId, { descQueryRaw: event.target.value, descQueryFinal: event.target.value })}
              />
              {draft.caseType !== "RELATIONAL" || subject.subjectId === 1 && draft.relationalSubject1ChangeEnabled || subject.subjectId === 2 && draft.relationalSubject2ChangeEnabled ? (
                <textarea
                  placeholder="CHANGE in target image"
                  value={subject.changeTargetFinal}
                  onChange={(event) => updateReviewSubject(subject.subjectId, { changeTargetRaw: event.target.value, changeTargetFinal: event.target.value })}
                />
              ) : null}
            </div>
          ))}
          {draft.caseType === "RELATIONAL" ? (
            <div className="quick-relation">
              <input
                placeholder="PAIR_CHANGE"
                value={draft.pairChangeFinal ?? ""}
                onChange={(event) => onChange((current) => ({ ...current, pairChangeRaw: event.target.value, pairChangeFinal: event.target.value }))}
              />
              <label><input type="checkbox" checked={draft.relationalSubject1ChangeEnabled} onChange={(event) => onChange((current) => ({ ...current, relationalSubject1ChangeEnabled: event.target.checked }))} /> Subject 1 extra CHANGE</label>
              <label><input type="checkbox" checked={draft.relationalSubject2ChangeEnabled} onChange={(event) => onChange((current) => ({ ...current, relationalSubject2ChangeEnabled: event.target.checked }))} /> Subject 2 extra CHANGE</label>
            </div>
          ) : null}
        </div>

        <div className="quick-caption">
          <span>Caption preview</span>
          <p>{captionPreview || "Missing required fields."}</p>
        </div>
        {item.issues.length ? <ul className="issues quick-issues">{item.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul> : null}
      </div>
    </article>
  );
}

function ReviewThumb({
  title,
  type,
  image,
  annotation,
  onToggleBox
}: {
  title: string;
  type: "QUERY" | "TARGET";
  image: TaskImage | null;
  annotation: Stage2Annotation;
  onToggleBox: (subjectId: number, boxId: string) => void;
}) {
  const boxes = image?.boxes ?? [];
  const subjectColors = ["#14b8a6", "#f59e0b", "#3b82f6", "#ef4444"];

  function ownerForBox(boxId: string): SubjectAnnotation | undefined {
    return annotation.subjects.find((subject) => {
      const ids = type === "QUERY" ? subject.queryGroupIds : subject.targetGroupIds;
      return ids.includes(boxId);
    });
  }

  return (
    <div className="review-thumb">
      <span>{title}</span>
      {image?.imageUrl ? (
        <div className="review-thumb-frame">
          <div className="review-image-wrap">
            <img src={fullImageUrl(image.imageUrl)} alt={title} />
            {boxes.map((box) => {
              const id = String(box.groupUid || box.label || box.id);
              const owner = ownerForBox(id);
              const subjectId = owner?.subjectId ?? annotation.subjects[0]?.subjectId ?? 1;
              const color = subjectColors[(subjectId - 1) % subjectColors.length];
              const selected = Boolean(owner);

              return (
                <button
                  className={`review-box ${selected ? "review-box-selected" : ""}`}
                  style={{
                    left: `${(box.x ?? 0) * 100}%`,
                    top: `${(box.y ?? 0) * 100}%`,
                    width: `${(box.width ?? 0) * 100}%`,
                    height: `${(box.height ?? 0) * 100}%`,
                    borderColor: selected ? color : "rgba(255, 255, 255, 0.55)",
                    backgroundColor: selected ? `${color}26` : "rgba(255, 255, 255, 0.04)"
                  }}
                  title={`${selected ? `Subject ${subjectId}` : "Add to Subject 1"} - Box ${id}`}
                  onClick={() => onToggleBox(subjectId, id)}
                  key={String(box.id)}
                  type="button"
                >
                  <span style={{ backgroundColor: selected ? color : "rgba(0, 0, 0, 0.65)" }}>
                    {selected ? `S${subjectId} ${id}` : id}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="review-thumb-empty">No image</div>
      )}
    </div>
  );
}

function splitIds(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
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
