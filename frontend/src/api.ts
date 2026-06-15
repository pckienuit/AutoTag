import type { AutomationMode, AutomationStatus, ReviewTask, Stage2Annotation, Task } from "./types";

const jsonHeaders = { "Content-Type": "application/json" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  }
  return data as T;
}

export const api = {
  settings: () => request<Record<string, unknown>>("/api/settings"),
  login: () => request("/api/uit/login", { method: "POST" }),
  sessions: () => request<{ sessions: unknown[] }>("/api/uit/sessions"),
  autoCurrentTask: () =>
    request<{ sessionId: string | null; task: Task | null }>("/api/uit/auto-current-task"),
  currentTask: (sessionId: string) =>
    request<{ task: Task | null }>(`/api/uit/sessions/${sessionId}/current-task`),
  task: (sessionId: string, taskId: string) =>
    request<{ task: Task }>(`/api/uit/sessions/${sessionId}/tasks/${taskId}`),
  submissions: (sessionId: string, sent = false) =>
    request<{ submissions: unknown[] }>(`/api/uit/sessions/${sessionId}/submissions?sent=${sent}`),
  startAutomation: (mode: AutomationMode, limit: number | null) =>
    request<AutomationStatus>("/api/automation/start", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ mode, limit })
    }),
  stopAutomation: () => request<AutomationStatus>("/api/automation/stop", { method: "POST" }),
  automationStatus: () => request<AutomationStatus>("/api/automation/status"),
  reviewTasks: (status = "active", limit = 120) =>
    request<{ tasks: ReviewTask[]; limit: number; total: number }>(
      `/api/review/tasks?status=${encodeURIComponent(status)}&limit=${limit}`
    ),
  importReviewTasks: (remoteUrl: string) =>
    request<{ status: string; source: string; imported: number; skipped: number }>("/api/review/import-remote", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ remoteUrl })
    }),
  approveReview: (sessionId: string, task: Task, annotation: Stage2Annotation, issues: string[] = []) =>
    request<{ status: string; issues?: string[]; caption?: string }>("/api/review/approve", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ sessionId, task, annotation, issues })
    }),
  generate: (task: Task, notes: string) =>
    request<{ annotation: Stage2Annotation; caption: string; issues: string[] }>("/api/ai/generate", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ task, notes })
    }),
  fixText: (text: string, field: string) =>
    request<{ text: string }>("/api/ai/fix-text", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ text, field })
    }),
  save: (sessionId: string, task: Task, annotation: Stage2Annotation, timeSpent = 0, reviewed = false) =>
    request<{ status: string; issues?: string[]; warnings?: string[]; caption?: string; task?: Task }>("/api/uit/save", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ sessionId, task, annotation, timeSpent, reviewed })
    }),
  submit: (sessionId: string, task: Task, annotation: Stage2Annotation, timeSpent = 0) =>
    request<{ status: string; issues?: string[] }>("/api/uit/submit", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ sessionId, task, annotation, timeSpent })
    }),
  localTasks: () => request<{ tasks: unknown[] }>("/api/local/tasks")
};
