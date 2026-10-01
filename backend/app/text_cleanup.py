import re


CONNECTOR_WORDS = ("and", "or", "but", "nor", "yet", "so")
COMMA_BEFORE_CONNECTOR_RE = re.compile(
    r",\s+(?=(" + "|".join(CONNECTOR_WORDS) + r")\b)",
    re.IGNORECASE,
)
CONDITION_PREFIX_RE = re.compile(r"^then retrieve target images where\s+", re.IGNORECASE)


def remove_comma_before_connectors(text: str | None) -> str:
    return COMMA_BEFORE_CONNECTOR_RE.sub(" ", str(text or ""))


def clean_slot(text: str | None) -> str:
    """Mirror the annotator page: collapse whitespace and drop trailing punctuation."""
    value = " ".join(str(text or "").split())
    return value.rstrip(".;, ").strip()


def condition_body(text: str | None) -> str:
    return CONDITION_PREFIX_RE.sub("", clean_slot(text))


def cleanup_select_text(text: str | None) -> str:
    return clean_slot(remove_comma_before_connectors(text))


def cleanup_target_condition(text: str | None) -> str:
    return condition_body(remove_comma_before_connectors(text))
