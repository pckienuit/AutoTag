export type CaseType = "SINGLE" | "MULTI" | "RELATIONAL";

export interface Box {
  id: string;
  groupUid?: string;
  label?: string;
  rawLabel?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
}

export interface TaskImage {
  side: string;
  imageUrl?: string;
  boxes?: Box[];
}

export interface Task {
  id: string;
  orderIndex?: number;
  images?: TaskImage[];
  blocks?: unknown;
  claimToken?: string;
  reservationVersion?: number;
  draftVersion?: number;
  canEdit?: boolean;
  status?: string;
}

export interface SubjectAnnotation {
  subjectId: number;
  targetConstraintEnabled: boolean;
  queryGroupIds: string[];
  targetGroupIds: string[];
  descQueryRaw: string;
  descQueryFinal: string;
  changeTargetRaw: string;
  changeTargetFinal: string;
}

export interface Stage2Annotation {
  schemaVersion: "1.0";
  caseType: CaseType;
  targetConstraintEnabled: boolean;
  relationalSubject1ChangeEnabled: boolean;
  relationalSubject2ChangeEnabled: boolean;
  captionRaw: string | null;
  captionFinal: string | null;
  pairChangeRaw: string | null;
  pairChangeFinal: string | null;
  llmEdits: Array<Record<string, unknown>>;
  subjects: SubjectAnnotation[];
}

export interface Session {
  id: string;
  name?: string;
  poolName?: string;
  batchName?: string;
  completed?: number;
  total?: number;
  availableTaskCount?: number;
  draftTaskId?: string;
}

