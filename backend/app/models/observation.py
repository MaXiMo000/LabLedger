from datetime import datetime
from typing import Annotated, ClassVar, Literal

import pymongo
from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field

from app.models.user import utcnow

MappingStage = Literal[
    "alias",                 # user-confirmed lookup, conf 1.00
    "exact",                 # exact hit on a primary LOINC name, conf 0.95
    "related_corroborated",  # related-name hit + specimen + unit agree; always reviewed
    "narrowed_fuzzy",
    "llm",
    "unmapped",
]
ReviewStatus = Literal["auto", "pending", "confirmed", "rejected"]
Flag = Literal["low", "normal", "high", "abnormal", "unknown"]


class MappingProvenance(BaseModel):
    """Why this row resolved the way it did. Every derived field must be explainable."""

    stage: MappingStage = "unmapped"
    confidence: float = 0.0
    candidates_considered: int = 0
    llm_model: str | None = None
    decided_at: datetime = Field(default_factory=utcnow)
    confirmed_by_user_at: datetime | None = None
    note: str | None = None


class Observation(Document):
    """One lab result row: what was printed, and what it resolved to."""

    patient_id: Annotated[PydanticObjectId, Indexed()]
    document_id: PydanticObjectId

    # --- exactly as printed. Never mutated; this is the audit anchor. ---
    raw_name: str
    raw_value: str
    raw_unit: str | None = None
    raw_ref_range: str | None = None
    raw_specimen: str | None = None
    raw_flag: str | None = None
    page: int = 1
    line_no: int = 0

    # --- resolved ---
    loinc_code: str | None = None
    loinc_display: str | None = None

    value_num: float | None = None   # None for qualitative results
    value_text: str | None = None    # "NEGATIVE", "TRACE", ...
    value_operator: str | None = None  # "<" or ">" when the lab censored the value

    canonical_unit: str | None = None
    canonical_value: float | None = None
    unit_conversion_factor: float | None = None

    ref_low: float | None = None
    ref_high: float | None = None
    ref_source: Literal["pdf", "builtin", "none"] = "none"
    flag: Flag = "unknown"

    collected_at: datetime | None = None

    mapping: MappingProvenance = Field(default_factory=MappingProvenance)
    review_status: ReviewStatus = "auto"

    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "observations"
        indexes: ClassVar = [
            # the trend query
            [("patient_id", pymongo.ASCENDING), ("loinc_code", pymongo.ASCENDING),
             ("collected_at", pymongo.ASCENDING)],
            [("patient_id", pymongo.ASCENDING), ("review_status", pymongo.ASCENDING)],
            [("document_id", pymongo.ASCENDING)],
        ]
