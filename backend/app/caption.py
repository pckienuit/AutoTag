import re

from .models import RcrAnnotation, TWO_SUBJECT_CASES
from .text_cleanup import clean_slot, condition_body


PLACEHOLDER_CHARS = ("[", "]", "…")
NON_ENGLISH_RE = re.compile(r"[^\x00-\x7f]")
SUBJECT_LABEL_RE = re.compile(r"\bsubjects?\s*(?:1|2|one|two)?\b", re.IGNORECASE)
INSTRUCTION_ECHO_RE = re.compile(r"retrieve target images|identify subject", re.IGNORECASE)
MAX_TEXT_LENGTH = 400


def is_two_subject(case_type: str) -> bool:
    return case_type in TWO_SUBJECT_CASES


def build_instruction(annotation: RcrAnnotation) -> str:
    """Final sentence shown by the annotator page (renderPreview in app.js)."""
    texts = [clean_slot(text) for text in annotation.select_texts]
    desc1 = texts[0] if texts else ""
    desc2 = texts[1] if len(texts) > 1 else ""
    description = f"Identify Subject 1 as {desc1 or '[…]'}"
    if is_two_subject(annotation.case_type):
        description += f" and Subject 2 as {desc2 or '[…]'}"
    condition = condition_body(annotation.target_condition)
    return f"{description}; then retrieve target images where {condition or '[…]'}."


def expected_case_type(initial_subjects: list[dict], case_type: str) -> str:
    """INDIVIDUAL and GROUP differ only by whether Subject 1 has several identities."""
    if case_type in TWO_SUBJECT_CASES:
        return case_type
    first = next((item for item in initial_subjects if item.get("subject_id") == 1), None)
    return "GROUP" if first and len(first.get("identity_ids") or []) > 1 else "INDIVIDUAL"


def validate_annotation(
    annotation: RcrAnnotation,
    candidate_identity_ids: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    two = is_two_subject(annotation.case_type)
    count = 2 if two else 1

    subjects = sorted(annotation.subjects, key=lambda item: item.subject_id)
    if [item.subject_id for item in subjects] != list(range(1, count + 1)):
        issues.append(f"{annotation.case_type} requires subjects {', '.join(map(str, range(1, count + 1)))}.")
    seen: set[str] = set()
    for subject in subjects:
        if not subject.identity_ids:
            issues.append(f"Subject {subject.subject_id} has no identity assigned.")
        overlap = seen.intersection(subject.identity_ids)
        if overlap:
            issues.append(f"Identity assigned twice: {', '.join(sorted(overlap))}.")
        seen.update(subject.identity_ids)
        if candidate_identity_ids is not None:
            unknown = set(subject.identity_ids) - set(candidate_identity_ids)
            if unknown:
                issues.append(f"Unknown identity for Subject {subject.subject_id}: {', '.join(sorted(unknown))}.")
    if not two and subjects and annotation.case_type == "GROUP" and len(subjects[0].identity_ids) < 2:
        issues.append("GROUP requires Subject 1 to have at least 2 identities.")
    if not two and subjects and annotation.case_type == "INDIVIDUAL" and len(subjects[0].identity_ids) > 1:
        issues.append("INDIVIDUAL requires Subject 1 to have exactly 1 identity.")

    if len(annotation.select_texts) != count:
        issues.append(f"{annotation.case_type} requires {count} SELECT text(s).")
    for index, text in enumerate(annotation.select_texts, start=1):
        value = clean_slot(text)
        if not value:
            issues.append(f"Subject {index} SELECT text is empty.")
            continue
        issues.extend(text_issues(value, f"Subject {index} SELECT"))
        if SUBJECT_LABEL_RE.search(value):
            issues.append(f"Subject {index} SELECT must not contain the word 'Subject'.")

    target = condition_body(annotation.target_condition)
    if not target:
        issues.append("Target condition is empty.")
    else:
        issues.extend(text_issues(target, "Target condition"))
        labels = set(re.findall(r"\bsubject\s*(1|2)\b", target, re.IGNORECASE))
        if "1" not in labels:
            issues.append("Target condition must mention Subject 1.")
        if two and "2" not in labels:
            issues.append("Target condition must mention Subject 2.")
        if not two and "2" in labels:
            issues.append("Target condition must not mention Subject 2.")
    if two and target:
        issues.extend(case_semantic_issues(annotation.case_type, target))
    return issues


def case_semantic_issues(case_type: str, target: str) -> list[str]:
    """Conservative text guard; visual/semantic review is still required.

    Match ordered predicates attached to explicit subjects, not arbitrary words
    such as 'holding a baby' or a third person's action later in the sentence.
    This is not a complete English parser and must not claim visual truth.
    """
    text = target.lower()
    directed = False
    symmetric = False
    for actor, other in (("1", "2"), ("2", "1")):
        for match in re.finditer(r"subject\s+" + actor + r"\b(.*?)(?=subject\s+[12]\b|$)", text):
            predicate = match.group(1)
            rest = text[match.end():]
            if not re.match(r"subject\s+" + other + r"\b", rest):
                continue
            # Do not attribute a third party's action to the preceding subject.
            predicate = re.split(r"\b(?:while|when)\s+(?:a|an|the)\b", predicate)[0]
            if re.search(r"(?:next to|near|beside|alongside)\s*$", predicate):
                symmetric = True
            if re.search(r"(?:holding|holds|carrying|carries|feeding|feeds|following|follows|behind|in front of|to the left of|to the right of|left of|right of)\s*$", predicate):
                directed = True
            if re.search(r"(?:handing|giving|presenting|offering|feeding|pointing|looking|resting|leaning|reaching).*?(?:to|at|against|toward|towards|on)\s*$", predicate):
                directed = True
    if case_type == "RELATIONAL" and symmetric and not directed:
        return ["RELATIONAL needs a directed Subject 1-to-Subject 2 relation whose truth changes when subjects are swapped; next to/near/beside alone is insufficient."]
    if case_type == "DUAL" and directed:
        return ["DUAL needs independent detailed predicates for each subject, not an ordered intersubject relationship as its defining condition."]
    return []


def text_issues(value: str, label: str) -> list[str]:
    issues: list[str] = []
    if any(char in value for char in PLACEHOLDER_CHARS):
        issues.append(f"{label} has an unresolved placeholder.")
    if NON_ENGLISH_RE.search(value):
        issues.append(f"{label} must be English (ASCII) text.")
    if INSTRUCTION_ECHO_RE.search(value):
        issues.append(f"{label} repeats the instruction template.")
    if len(value) > MAX_TEXT_LENGTH:
        issues.append(f"{label} is too long.")
    return issues
