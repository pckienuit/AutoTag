import type {
  AppSettings,
  AutomationMode,
  AutomationStatus,
  CaseType,
  PushResult,
  QueueItem,
  RcrAnnotation,
  TaskDetail
} from "./types";

const jsonHeaders = { "Content-Type": "application/json" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  let data: Record<string, unknown> = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Invalid response (HTTP ${response.status})`);
  }
  if (!response.ok) {
    throw new Error(String(data.detail ?? data.message ?? `HTTP ${response.status}`));
  }
  return data as T;
}

const post = <T>(url: string, body?: unknown) =>
  request<T>(url, { method: "POST", headers: jsonHeaders, body: body === undefined ? undefined : JSON.stringify(body) });

export const imageUrl = (sampleId: string, side: "query" | "target") =>
  `/api/rcr/images/${encodeURIComponent(sampleId)}/${side}`;

export const api = {
  settings: () => request<AppSettings>("/api/settings"),
  queue: () => request<{ tasks: QueueItem[] }>("/api/rcr/tasks"),
  task: (sampleId: string) => request<TaskDetail>(`/api/rcr/tasks/${encodeURIComponent(sampleId)}`),
  generate: (sampleId: string, notes: string) =>
    post<{ annotation: RcrAnnotation; instruction: string; issues: string[]; concerns: string[] }>("/api/ai/generate", {
      sample_id: sampleId,
      notes
    }),
  validate: (sampleId: string, annotation: RcrAnnotation) =>
    post<{ instruction: string; issues: string[] }>("/api/ai/validate", { sample_id: sampleId, annotation }),
  saveDraft: (sampleId: string, annotation: RcrAnnotation) =>
    post<PushResult>("/api/rcr/draft", { sample_id: sampleId, annotation }),
  submit: (sampleId: string, annotation: RcrAnnotation) =>
    post<PushResult>("/api/rcr/submit", { sample_id: sampleId, annotation }),
  reopen: (sampleId: string) =>
    post<{ task: TaskDetail["task"]; annotation: RcrAnnotation | null }>(
      `/api/rcr/tasks/${encodeURIComponent(sampleId)}/reopen`
    ),
  startAutomation: (mode: AutomationMode, options: { limit?: number; caseType?: CaseType; sampleId?: string }) =>
    post<AutomationStatus>("/api/automation/start", {
      mode,
      limit: options.limit ?? null,
      case_type: options.caseType ?? null,
      sample_id: options.sampleId ?? null
    }),
  stopAutomation: () => post<AutomationStatus>("/api/automation/stop"),
  automationStatus: () => request<AutomationStatus>("/api/automation/status")
};
