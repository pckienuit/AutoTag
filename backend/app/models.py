from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal


CaseType = Literal["INDIVIDUAL", "GROUP", "DUAL", "RELATIONAL"]
CASE_TYPES: tuple[str, ...] = ("INDIVIDUAL", "GROUP", "DUAL", "RELATIONAL")
TWO_SUBJECT_CASES = {"DUAL", "RELATIONAL"}
AutomationMode = Literal["all_open", "one_case", "fixed_limit", "single_task"]
AUTOMATION_MODES: tuple[str, ...] = ("all_open", "one_case", "fixed_limit", "single_task")


@dataclass
class RuntimeSettings:
    openai_compat_base_url: str = "https://api.openai.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "ag/gemini-3.8-flash-high"
    openai_compat_review_model: str = "ag/gemini-pro-agent"
    auto_submit_enabled: bool = False
    ai_double_check_enabled: bool = True


@dataclass
class SubjectAssignment:
    subject_id: int
    identity_ids: list[str] = field(default_factory=list)


@dataclass
class RcrAnnotation:
    case_type: str
    subjects: list[SubjectAssignment] = field(default_factory=list)
    select_texts: list[str] = field(default_factory=list)
    target_condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def replace(self, **changes: Any) -> "RcrAnnotation":
        return replace(self, **changes)

    @classmethod
    def from_dict(cls, data: Any) -> "RcrAnnotation":
        """Build an annotation from untrusted JSON; raise ValueError with a readable message."""
        if not isinstance(data, dict):
            raise ValueError("annotation must be an object.")
        case_type = data.get("case_type")
        if case_type not in CASE_TYPES:
            raise ValueError(f"case_type must be one of {', '.join(CASE_TYPES)}.")
        raw_subjects = data.get("subjects", [])
        if not isinstance(raw_subjects, list):
            raise ValueError("subjects must be a list.")
        subjects = []
        for item in raw_subjects:
            if not isinstance(item, dict) or not isinstance(item.get("subject_id"), int):
                raise ValueError("each subject needs an integer subject_id.")
            identity_ids = item.get("identity_ids", [])
            if not isinstance(identity_ids, list):
                raise ValueError("identity_ids must be a list.")
            subjects.append(
                SubjectAssignment(subject_id=item["subject_id"], identity_ids=[str(value) for value in identity_ids])
            )
        select_texts = data.get("select_texts", [])
        if not isinstance(select_texts, list) or not all(isinstance(text, str) for text in select_texts):
            raise ValueError("select_texts must be a list of strings.")
        target_condition = data.get("target_condition", "")
        if not isinstance(target_condition, str):
            raise ValueError("target_condition must be a string.")
        return cls(case_type, subjects, list(select_texts), target_condition)


@dataclass
class AutomationStartRequest:
    mode: str = "all_open"
    limit: int | None = None
    case_type: str | None = None
    sample_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutomationStartRequest":
        mode = data.get("mode", "all_open")
        if mode not in AUTOMATION_MODES:
            raise ValueError(f"mode must be one of {', '.join(AUTOMATION_MODES)}.")
        limit = data.get("limit")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)):
            raise ValueError("limit must be an integer.")
        case_type = data.get("case_type")
        if case_type is not None and case_type not in CASE_TYPES:
            raise ValueError(f"case_type must be one of {', '.join(CASE_TYPES)}.")
        sample_id = data.get("sample_id")
        if sample_id is not None and not isinstance(sample_id, str):
            raise ValueError("sample_id must be a string.")
        return cls(mode, limit, case_type, sample_id)


def require_sample_id(data: dict[str, Any]) -> str:
    sample_id = data.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id:
        raise ValueError("sample_id is required.")
    return sample_id


def require_bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    return value
