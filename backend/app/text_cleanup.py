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


def remove_comma_before_connectors(text: str | None) -> str:
    return COMMA_BEFORE_CONNECTOR_RE.sub(" ", str(text or ""))


def cleanup_annotation_text(value: Any) -> Any:
    if isinstance(value, str):
        return remove_comma_before_connectors(value)
    if isinstance(value, list):
        return [cleanup_annotation_text(item) for item in value]
    if isinstance(value, dict):
        return {key: cleanup_annotation_text(item) for key, item in value.items()}
    return value
