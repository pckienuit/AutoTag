from typing import Any, Literal

from pydantic import BaseModel, Field


CaseType = Literal["SINGLE", "MULTI", "RELATIONAL"]


class RuntimeSettings(BaseModel):
    openai_compat_base_url: str = "https://api.openai.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "gpt-4o-mini"
    auto_submit_enabled: bool = False


AutomationMode = Literal["all_open", "one_session", "fixed_limit", "current_task"]


class AutomationStartRequest(BaseModel):
    mode: AutomationMode = "all_open"
    limit: int | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class SubjectAnnotation(BaseModel):
    subjectId: int
    targetConstraintEnabled: bool = True
    queryGroupIds: list[str] = Field(default_factory=list)
    targetGroupIds: list[str] = Field(default_factory=list)
    descQueryRaw: str = ""
    descQueryFinal: str = ""
    changeTargetRaw: str = ""
    changeTargetFinal: str = ""


class Stage2Annotation(BaseModel):
    schemaVersion: str = "1.0"
    caseType: CaseType = "SINGLE"
    targetConstraintEnabled: bool = True
    relationalSubject1ChangeEnabled: bool = False
    relationalSubject2ChangeEnabled: bool = False
    captionRaw: str | None = None
    captionFinal: str | None = None
    pairChangeRaw: str | None = None
    pairChangeFinal: str | None = None
    llmEdits: list[dict[str, Any]] = Field(default_factory=list)
    subjects: list[SubjectAnnotation] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    task: dict[str, Any]
    notes: str = ""


class FixTextRequest(BaseModel):
    text: str
    field: str = "annotation"


class ReviewApproveRequest(BaseModel):
    task: dict[str, Any]
    annotation: Stage2Annotation
    sessionId: str
    issues: list[str] = Field(default_factory=list)


class ReviewImportRequest(BaseModel):
    remoteUrl: str | None = None


class SyncRequest(BaseModel):
    task: dict[str, Any]
    annotation: Stage2Annotation
    timeSpent: int = 0
    sessionId: str | None = None
    reviewed: bool = False


class TaskEnvelope(BaseModel):
    sessionId: str | None = None
    task: dict[str, Any] | None = None


class ImageAsset(BaseModel):
    side: str
    imageUrl: str | None = None
    boxes: list[dict[str, Any]] = Field(default_factory=list)
