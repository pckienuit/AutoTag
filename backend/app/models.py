from typing import Literal

from pydantic import BaseModel, Field


CaseType = Literal["INDIVIDUAL", "GROUP", "DUAL", "RELATIONAL"]
CASE_TYPES: tuple[str, ...] = ("INDIVIDUAL", "GROUP", "DUAL", "RELATIONAL")
TWO_SUBJECT_CASES = {"DUAL", "RELATIONAL"}


class RuntimeSettings(BaseModel):
    openai_compat_base_url: str = "https://api.openai.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "ag/gemini-3.8-flash-high"
    openai_compat_review_model: str = "ag/gemini-pro-agent"
    auto_submit_enabled: bool = False
    ai_double_check_enabled: bool = True


AutomationMode = Literal["all_open", "one_case", "fixed_limit", "single_task"]


class AutomationStartRequest(BaseModel):
    mode: AutomationMode = "all_open"
    limit: int | None = None
    case_type: CaseType | None = None
    sample_id: str | None = None


class SubjectAssignment(BaseModel):
    subject_id: int
    identity_ids: list[str] = Field(default_factory=list)


class RcrAnnotation(BaseModel):
    case_type: CaseType
    subjects: list[SubjectAssignment] = Field(default_factory=list)
    select_texts: list[str] = Field(default_factory=list)
    target_condition: str = ""


class GenerateRequest(BaseModel):
    sample_id: str
    notes: str = ""


class SaveRequest(BaseModel):
    sample_id: str
    annotation: RcrAnnotation
    revision: int | None = None


class ApproveRequest(SaveRequest):
    pass
