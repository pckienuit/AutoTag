import type { CaseType, RcrAnnotation, SubjectAssignment, Task } from "./types";

export const isTwoSubject = (caseType: CaseType) => caseType === "DUAL" || caseType === "RELATIONAL";

const cleanSlot = (value: string) => value.replace(/\s+/g, " ").trim().replace(/[.;,\s]+$/g, "");
const conditionBody = (value: string) => cleanSlot(value).replace(/^then retrieve target images where\s+/i, "");

/** Same sentence the annotator page shows as "Final instruction". */
export function buildInstruction(annotation: RcrAnnotation): string {
  const [first = "", second = ""] = annotation.select_texts.map(cleanSlot);
  const description =
    `Identify Subject 1 as ${first || "[…]"}` +
    (isTwoSubject(annotation.case_type) ? ` and Subject 2 as ${second || "[…]"}` : "");
  return `${description}; then retrieve target images where ${conditionBody(annotation.target_condition) || "[…]"}.`;
}

export function blankAnnotation(task: Task): RcrAnnotation {
  const two = isTwoSubject(task.case_type);
  const subjects: SubjectAssignment[] = task.initial_subjects.length
    ? task.initial_subjects.map((item) => ({ ...item, identity_ids: [...item.identity_ids] }))
    : [{ subject_id: 1, identity_ids: [] }, ...(two ? [{ subject_id: 2, identity_ids: [] }] : [])];
  return syncCase({
    case_type: task.case_type,
    subjects,
    select_texts: subjects.map(() => ""),
    target_condition: ""
  });
}

/** One-subject cases are INDIVIDUAL or GROUP depending on how many identities Subject 1 has. */
export function syncCase(annotation: RcrAnnotation): RcrAnnotation {
  if (isTwoSubject(annotation.case_type)) return annotation;
  const count = annotation.subjects[0]?.identity_ids.length ?? 0;
  return { ...annotation, case_type: count > 1 ? "GROUP" : "INDIVIDUAL" };
}

export function assignmentOf(annotation: RcrAnnotation): Map<string, number> {
  const map = new Map<string, number>();
  for (const subject of annotation.subjects) {
    for (const id of subject.identity_ids) map.set(id, subject.subject_id);
  }
  return map;
}

export function toggleIdentity(annotation: RcrAnnotation, identityId: string, subjectId: number): RcrAnnotation {
  const owner = assignmentOf(annotation).get(identityId);
  const subjects = annotation.subjects.map((subject) => {
    const without = subject.identity_ids.filter((id) => id !== identityId);
    if (subject.subject_id === subjectId && owner !== subjectId) {
      return { ...subject, identity_ids: [...without, identityId] };
    }
    return { ...subject, identity_ids: without };
  });
  return syncCase({ ...annotation, subjects });
}

export function setCaseMode(annotation: RcrAnnotation, mode: "one" | "DUAL" | "RELATIONAL"): RcrAnnotation {
  if (mode === "one") {
    return syncCase({
      ...annotation,
      case_type: "INDIVIDUAL",
      subjects: annotation.subjects.slice(0, 1),
      select_texts: annotation.select_texts.slice(0, 1)
    });
  }
  const subjects = [
    annotation.subjects[0] ?? { subject_id: 1, identity_ids: [] },
    annotation.subjects[1] ?? { subject_id: 2, identity_ids: [] }
  ];
  return {
    ...annotation,
    case_type: mode,
    subjects,
    select_texts: [annotation.select_texts[0] ?? "", annotation.select_texts[1] ?? ""]
  };
}
