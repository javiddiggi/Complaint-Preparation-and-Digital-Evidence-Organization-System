from datetime import date as DateValue, datetime, time as TimeValue
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IncidentFields(BaseModel):
    incident_type: str | None = Field(default=None, max_length=120)
    incident_date: DateValue | None = None
    incident_time: TimeValue | None = None
    platform: str | None = Field(default=None, max_length=255)
    reported_identifier: str | None = Field(default=None, max_length=2048)
    transaction_reference: str | None = Field(default=None, max_length=255)
    loss_amount: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=255)
    summary: str | None = Field(default=None, max_length=20000)
    requested_action: str | None = Field(default=None, max_length=10000)

    model_config = ConfigDict(extra="forbid")


class IncidentCreate(IncidentFields):
    pass


class IncidentUpdate(IncidentFields):
    pass


class IncidentRead(IncidentFields):
    id: str
    created_at: datetime
    updated_at: datetime
    workflow_data: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class IncidentMutationResponse(BaseModel):
    message: str
    incident: IncidentRead


class IncidentDeleteResponse(BaseModel):
    message: str
    id: str


EvidenceCategory = Literal[
    "Communication",
    "Financial or transaction",
    "Website or URL",
    "Incident document",
    "Other",
]

EvidenceExtractionStatus = Literal[
    "not_extracted",
    "extracted",
    "ocr_extracted",
    "ocr_required",
    "empty",
    "password_protected",
    "unsupported",
    "error",
]


class EvidenceRead(BaseModel):
    id: str
    incident_id: str
    original_filename: str
    stored_filename: str
    file_type: str
    file_size: int
    uploaded_at: datetime
    category: EvidenceCategory
    extraction_status: EvidenceExtractionStatus
    extraction_message: str | None
    extracted_at: datetime | None
    structured_data: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceTextRead(BaseModel):
    evidence_id: str
    status: EvidenceExtractionStatus
    extracted_text: str | None
    message: str | None
    extracted_at: datetime | None


class TimelineEventInput(BaseModel):
    date: DateValue | None = None
    date_text: str | None = Field(default=None, max_length=100)
    time: TimeValue | None = None
    description: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="User entry", max_length=255)
    evidence_id: str | None = Field(default=None, max_length=36)
    evidence_filename: str | None = Field(default=None, max_length=255)
    origin: Literal["user", "evidence", "incident"] = "user"
    review_status: Literal["reported", "candidate", "reviewed"] | None = None

    model_config = ConfigDict(extra="forbid")


class TimelineUpdate(BaseModel):
    events: list[TimelineEventInput] = Field(max_length=500)

    model_config = ConfigDict(extra="forbid")


class ComplaintUpdate(BaseModel):
    text: str = Field(max_length=100000)
    reviewed: bool = False

    model_config = ConfigDict(extra="forbid")


class GuidanceSearch(BaseModel):
    query: str = Field(min_length=2, max_length=500)

    model_config = ConfigDict(extra="forbid")


class ExternalAIConsent(BaseModel):
    consent_external_ai: Literal[True]

    model_config = ConfigDict(extra="forbid")


class EvidenceFactsReview(BaseModel):
    confirmed_fact_indices: list[int] = Field(max_length=200)

    model_config = ConfigDict(extra="forbid")


class EvidenceDeleteResponse(BaseModel):
    message: str
    id: str
