import type { Stage2Annotation, Task } from "./types";

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
  generate: (task: Task, notes: string) =>
    request<{ annotation: Stage2Annotation; caption: string; issues: string[] }>("/api/ai/generate", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ task, notes })
    }),
  save: (sessionId: string, task: Task, annotation: Stage2Annotation, timeSpent = 0) =>
    request<{ status: string; issues?: string[]; caption?: string }>("/api/uit/save", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ sessionId, task, annotation, timeSpent })
    }),
  submit: (sessionId: string, task: Task, annotation: Stage2Annotation, timeSpent = 0) =>
    request<{ status: string; issues?: string[] }>("/api/uit/submit", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ sessionId, task, annotation, timeSpent })
    }),
  localTasks: () => request<{ tasks: unknown[] }>("/api/local/tasks")
};
