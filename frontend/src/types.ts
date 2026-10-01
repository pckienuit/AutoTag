export type CaseType = "INDIVIDUAL" | "GROUP" | "DUAL" | "RELATIONAL";
export const CASE_TYPES: CaseType[] = ["INDIVIDUAL", "GROUP", "DUAL", "RELATIONAL"];

export interface Box {
  identity_id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface TaskImage {
  image_id: string;
  image_url: string;
  boxes: Box[];
}

export interface SubjectAssignment {
  subject_id: number;
  identity_ids: string[];
}

export interface Task {
  sample_id: string;
  case_type: CaseType;
  split?: string;
  status: "UNASSIGNED" | "ASSIGNED" | "IN_PROGRESS" | "SUBMITTED";
  revision: number;
  query: TaskImage;
  target: TaskImage;
  candidate_identity_ids: string[];
  initial_subjects: SubjectAssignment[];
}

export interface QueueItem {
  sample_id: string;
  case_type: CaseType;
  split?: string;
  status: Task["status"];
  revision: number;
  localStatus: LocalStatus | null;
}

export interface RcrAnnotation {
  case_type: CaseType;
  subjects: SubjectAssignment[];
  select_texts: string[];
  target_condition: string;
}

export type LocalStatus = "generated" | "needs_review" | "draft_saved" | "submitted" | "failed" | "reviewed";

export interface LocalTask {
  sampleId: string;
  status: LocalStatus;
  annotation: RcrAnnotation | null;
  instruction: string;
  issues: string[];
  concerns?: string[];
  error: string | null;
  updatedAt: string;
}

export interface TaskDetail {
  task: Task;
  annotation: RcrAnnotation | null;
  local: LocalTask | null;
}

export type AutomationMode = "all_open" | "one_case" | "fixed_limit" | "single_task";

export interface AutomationStatus {
  running: boolean;
  stop_requested: boolean;
  mode: AutomationMode | null;
  limit: number | null;
  case_type: CaseType | null;
  total: number;
  processed: number;
  drafted: number;
  submitted: number;
  failed: number;
  needs_review: number;
  current_task_id: string | null;
  message: string;
  next_delay_seconds: number | null;
  next_delay_until: number | null;
}

export interface AppSettings {
  rcrBaseUrl: string;
  model: string;
  reviewModel: string;
  autoSubmitEnabled: boolean;
  aiDoubleCheckEnabled: boolean;
  apiKeyConfigured: boolean;
}

export interface PushResult {
  status: "draft_saved" | "submitted" | "blocked";
  issues?: string[];
  task?: Task;
  instruction?: string;
}
