from .models import Stage2Annotation, SubjectAnnotation
from .text_cleanup import remove_comma_before_connectors


PLACEHOLDER_CHARS = ("[", "]")


def clean_fragment(value: str | None) -> str:
    text = str(value or "").strip()
    text = " ".join(text.replace(";", ",").split())
    text = remove_comma_before_connectors(text)
    return text.strip(" ,.!?")


def normalize_change(value: str | None) -> str:
    text = clean_fragment(value)
    lowered = text.lower()
    for prefix in ("subject 1 ", "subject 2 "):
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip(" :-,")
            lowered = text.lower()
            break
    return text


def normalize_pair(value: str | None) -> str:
    text = clean_fragment(value)
    if not text:
        return ""
    if text.lower().startswith("subject 1 ") and " subject 2" in text.lower():
        return text
    if text.lower().startswith(("is ", "are ", "was ", "were ")):
        return f"Subject 1 {text} Subject 2"
    return f"Subject 1 {text} Subject 2"


def has_placeholder(value: str | None) -> bool:
    text = str(value or "")
    return any(char in text for char in PLACEHOLDER_CHARS)


def subject_text(subject: SubjectAnnotation) -> tuple[str, str]:
    desc = clean_fragment(subject.descQueryFinal or subject.descQueryRaw)
    change = normalize_change(subject.changeTargetFinal or subject.changeTargetRaw)
    return desc, change


def build_caption(annotation: Stage2Annotation) -> str:
    subjects = sorted(annotation.subjects, key=lambda item: item.subjectId)
    subject1 = next((item for item in subjects if item.subjectId == 1), None)
    subject2 = next((item for item in subjects if item.subjectId == 2), None)
    if subject1 is None:
        return ""

    desc1, change1 = subject_text(subject1)
    if annotation.caseType == "SINGLE":
        if not desc1 or not change1:
            return ""
        return (
            f"In the query image, Subject 1 refers to {desc1}. "
            f"Retrieve target images where Subject 1 {change1}."
        )

    if subject2 is None:
        return ""
    desc2, change2 = subject_text(subject2)
    if annotation.caseType == "MULTI":
        if not desc1 or not desc2 or not change1 or not change2:
            return ""
        return (
            f"In the query image, Subject 1 refers to {desc1}, and Subject 2 refers to {desc2}. "
            f"Retrieve target images where Subject 1 {change1} and Subject 2 {change2}."
        )

    pair = normalize_pair(annotation.pairChangeFinal or annotation.pairChangeRaw)
    if not desc1 or not desc2 or not pair:
        return ""
    extras: list[str] = []
    if annotation.relationalSubject1ChangeEnabled and change1:
        extras.append(f"Subject 1 {change1}")
    if annotation.relationalSubject2ChangeEnabled and change2:
        extras.append(f"Subject 2 {change2}")
    suffix = ""
    if len(extras) == 1:
        suffix = f", with {extras[0]}"
    elif len(extras) == 2:
        suffix = f", with {extras[0]} and {extras[1]}"
    return (
        f"In the query image, Subject 1 refers to {desc1}, and Subject 2 refers to {desc2}. "
        f"Retrieve target images where {pair}{suffix}."
    )


def validate_annotation(annotation: Stage2Annotation) -> list[str]:
    issues: list[str] = []
    expected_count = 1 if annotation.caseType == "SINGLE" else 2
    subjects = sorted(annotation.subjects, key=lambda item: item.subjectId)
    if len(subjects) != expected_count:
        issues.append(f"{annotation.caseType} requires exactly {expected_count} subject(s).")
    seen_query_ids: set[str] = set()
    for subject in subjects:
        desc, change = subject_text(subject)
        if not subject.queryGroupIds:
            issues.append(f"Subject {subject.subjectId} must select at least one query group id.")
        overlap = seen_query_ids.intersection(subject.queryGroupIds)
        if overlap:
            issues.append(f"Subject group ids overlap: {', '.join(sorted(overlap))}.")
        seen_query_ids.update(subject.queryGroupIds)
        if not desc:
            issues.append(f"Subject {subject.subjectId} is missing DESC.")
        if has_placeholder(desc):
            issues.append(f"Subject {subject.subjectId} DESC has an unresolved placeholder.")
        change_required = annotation.caseType != "RELATIONAL" or (
            subject.subjectId == 1
            and annotation.relationalSubject1ChangeEnabled
            or subject.subjectId == 2
            and annotation.relationalSubject2ChangeEnabled
        )
        if change_required and not change:
            issues.append(f"Subject {subject.subjectId} is missing CHANGE.")
        if has_placeholder(change):
            issues.append(f"Subject {subject.subjectId} CHANGE has an unresolved placeholder.")
    if annotation.caseType == "RELATIONAL":
        pair = normalize_pair(annotation.pairChangeFinal or annotation.pairChangeRaw)
        if not pair:
            issues.append("RELATIONAL annotation is missing PAIR_CHANGE.")
        if has_placeholder(pair):
            issues.append("PAIR_CHANGE has an unresolved placeholder.")
    caption = build_caption(annotation)
    if not caption:
        issues.append("Caption could not be built.")
    elif has_placeholder(caption):
        issues.append("Caption has an unresolved placeholder.")
    return issues
