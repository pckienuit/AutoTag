import re
from typing import Any


CONNECTOR_WORDS = (
    "and",
    "or",
    "but",
    "nor",
    "yet",
    "so",
)
COMMA_BEFORE_CONNECTOR_RE = re.compile(
    r",\s+(?=(" + "|".join(CONNECTOR_WORDS) + r")\b)",
    re.IGNORECASE,
)
SUBJECT_REF_PREFIX_RE = re.compile(
    r"^\s*(?:in the query image,\s*)?subject\s*(?:1|2|one|two)?\s+refers to\s+",
    re.IGNORECASE,
)
SUBJECT_VERB_PREFIX_RE = re.compile(
    r"^\s*subject\s*(?:1|2|one|two)?\s+(?=(?:is|are|was|were|has|have|wears|holds|stands|sits|walks)\b)",
    re.IGNORECASE,
)
TARGET_SUBJECT_PREFIX_RE = re.compile(
    r"^\s*(?:retrieve target images where\s+)?subject\s*(?:1|2|one|two)?\s+",
    re.IGNORECASE,
)
SUBJECT_POSSESSIVE_RE = re.compile(
    r"\bsubject\s*(?:1|2|one|two)?'s\s+",
    re.IGNORECASE,
)
SUBJECT_PREPOSITION_RE = re.compile(
    r"\s+(?:in|of|for|from|to|with)\s+subject\s*(?:1|2|one|two)?\b",
    re.IGNORECASE,
)
SUBJECT_LABEL_RE = re.compile(
    r"\bsubject\s*(?:1|2|one|two)?\b",
    re.IGNORECASE,
)
GENERIC_SUBJECT_RE = re.compile(r"\bsubjects\b|\bsubject\b", re.IGNORECASE)


def remove_comma_before_connectors(text: str | None) -> str:
    return COMMA_BEFORE_CONNECTOR_RE.sub(" ", str(text or ""))


def remove_subject_references(text: str | None) -> str:
    value = str(text or "")
    value = SUBJECT_REF_PREFIX_RE.sub("", value)
    value = SUBJECT_VERB_PREFIX_RE.sub("", value)
    value = TARGET_SUBJECT_PREFIX_RE.sub("", value)
    value = SUBJECT_POSSESSIVE_RE.sub("", value)
    value = SUBJECT_PREPOSITION_RE.sub("", value)
    value = SUBJECT_LABEL_RE.sub("", value)
    value = GENERIC_SUBJECT_RE.sub(
        lambda match: "people" if match.group(0).lower().endswith("s") else "person",
        value,
    )
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" ,.;:-")


def cleanup_desc_change_text(text: str | None) -> str:
    return remove_subject_references(remove_comma_before_connectors(text))


def cleanup_annotation_text(value: Any) -> Any:
    if isinstance(value, str):
        return remove_comma_before_connectors(value)
    if isinstance(value, list):
        return [cleanup_annotation_text(item) for item in value]
    if isinstance(value, dict):
        return {key: cleanup_annotation_text(item) for key, item in value.items()}
    return value
