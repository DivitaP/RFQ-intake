from typing import Literal, Optional, Union

from pydantic import BaseModel, Field as PField

FIELDS = ["item_type", "size", "schedule", "pressure_class", "material", "end_connection", "quantity", "uom"]

Source = Literal["rule", "learned", "llm", "ocr", "human", "none"]
Value = Optional[Union[str, float]]


class FieldValue(BaseModel):
    value: Value = None
    source: Source = "none"
    confidence: float = 0.0
    required: bool = False
    issues: list[str] = PField(default_factory=list)
    evidence: Optional[str] = None  # substring of the raw line the LLM cited; becomes a learned alias on approval


class Candidate(BaseModel):
    sku: str
    description: str
    price: float
    stock: int
    diffs: list[dict] = PField(default_factory=list)  # [{field, want, have, note}]
    score: int = 0


class AgentVerdict(BaseModel):
    sku: Optional[str] = None
    verdict: Literal["equivalent", "upgrade", "not_acceptable", "no_match"] = "no_match"
    reason: str = ""
    steps: list[dict] = PField(default_factory=list)  # tool calls the agent made, for the UI trace


class CatalogMatch(BaseModel):
    status: Literal["exact", "learned", "similar", "none"] = "none"
    sku: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    stock: Optional[int] = None
    candidates: list[Candidate] = PField(default_factory=list)
    reason: str = ""
    agent: Optional[AgentVerdict] = None


class LineItem(BaseModel):
    line_no: int
    raw: str
    fields: dict[str, FieldValue]
    status: Literal["auto", "review"] = "review"
    item_issues: list[str] = PField(default_factory=list)
    match: Optional[CatalogMatch] = None
    origin: Optional[str] = None  # for emails: "email body" or "attachment: file.pdf"


class ExtractResult(BaseModel):
    doc_id: str
    filename: str
    kind: Literal["text", "scanned", "image", "email"]
    items: list[LineItem]
    warnings: list[str] = PField(default_factory=list)
    stats: dict = PField(default_factory=dict)
    email: Optional[dict] = None  # subject/from/date/body/attachments for .eml uploads
