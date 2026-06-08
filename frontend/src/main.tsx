import { Loader2, LogIn, RefreshCw, Save, Send, Settings, Sparkles } from "lucide-react";
import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import "./styles.css";
import type { Box, CaseType, Session, Stage2Annotation, SubjectAnnotation, Task, TaskImage } from "./types";

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
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [task, setTask] = useState<Task | null>(null);
  const [annotation, setAnnotation] = useState<Stage2Annotation>(emptyAnnotation());
  const [notes, setNotes] = useState("");
  const [issues, setIssues] = useState<string[]>([]);
  const [message, setMessage] = useState("Ready");
  const [busy, setBusy] = useState(false);
  const [activeSubjectId, setActiveSubjectId] = useState<number>(1);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  const caption = useMemo(() => buildCaption(annotation), [annotation]);

  useEffect(() => {
    void api.settings().then(setSettings).catch(() => undefined);
  }, []);

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
          <h2><Settings size={18} /> AI settings</h2>
          <input placeholder="Base URL" value={String(settings.openai_compat_base_url ?? "")} onChange={(event) => setSettings({ ...settings, openai_compat_base_url: event.target.value })} />
          <input placeholder="Model" value={String(settings.openai_compat_model ?? "")} onChange={(event) => setSettings({ ...settings, openai_compat_model: event.target.value })} />
          <input type="password" placeholder={settings.openai_compat_api_key ? "API key saved" : "API key"} onChange={(event) => setSettings({ ...settings, openai_compat_api_key: event.target.value })} />
          <button onClick={() => run("Saving settings", async () => { await api.saveSettings(settings); setMessage("Settings saved"); showToast("AI settings updated successfully", "success"); })}><Save size={16} />Save</button>
        </div>

        <div className="panel compact">
          <h2><LogIn size={18} /> UIT session</h2>
          <input placeholder="UIT email" value={email} onChange={(event) => setEmail(event.target.value)} />
          <input type="password" placeholder="UIT password" value={password} onChange={(event) => setPassword(event.target.value)} />
          <button onClick={() => run("Logging in", async () => { await api.login(email, password); const data = await api.sessions(); setSessions(data.sessions as Session[]); setMessage("Logged in"); showToast("Logged in to UIT portal successfully", "success"); })}><LogIn size={16} />Login</button>
          <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
            <option value="">Choose session</option>
            {sessions.map((session) => <option value={session.id} key={session.id}>{session.name || session.poolName || session.id}</option>)}
          </select>
          <button disabled={!sessionId} onClick={() => run("Loading task", async () => { const data = await api.currentTask(sessionId); setTask(data.task); setAnnotation(emptyAnnotation()); setIssues([]); setMessage(data.task ? "Task loaded" : "No current task"); showToast(data.task ? "Task loaded successfully" : "No active task in this session", data.task ? "success" : "info"); })}><RefreshCw size={16} />Current task</button>
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
          <button disabled={!task} onClick={() => run("Generating with AI", async () => { if (!task) return; const data = await api.generate(task, notes); setAnnotation(data.annotation); setIssues(data.issues); setMessage("AI draft ready"); showToast("AI Draft annotation generated", "success"); })}><Sparkles size={16} />Generate</button>
          <button disabled={!task} onClick={() => run("Saving to UIT", async () => { if (!task) return; const result = await api.save(sessionId, task, { ...annotation, captionFinal: caption }); setIssues(result.issues ?? []); setMessage(result.status); if (result.status === "saved") { showToast("Draft saved successfully to UIT" + (result.issues?.length ? " with warnings" : ""), result.issues?.length ? "info" : "success"); } else { showToast("Save failed", "error"); } })}><Save size={16} />Sync save</button>
          <button disabled={!task} onClick={() => run("Submitting", async () => { if (!task) return; const result = await api.submit(sessionId, task, { ...annotation, captionFinal: caption }); setIssues(result.issues ?? []); setMessage(result.status); if (result.status === "submitted") { showToast("Annotation submitted successfully!", "success"); const next = await api.currentTask(sessionId); setTask(next.task); setAnnotation(emptyAnnotation()); } else { showToast("Submission blocked: please fix issues", "error"); } })}><Send size={16} />Submit & next</button>
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
      {image?.imageUrl ? <img src={fullImageUrl(image.imageUrl)} alt={title} /> : <div className="empty">No image loaded</div>}
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

