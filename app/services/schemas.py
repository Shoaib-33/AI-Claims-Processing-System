from __future__ import annotations

from pydantic import BaseModel, Field


class InvoiceFields(BaseModel):
    raw_text: str = ""
    patient_name: str | None = None
    date_of_birth: str | None = None
    address: str | None = None
    phone: str | None = None
    date_of_treatment: str | None = None
    diagnosis: str | None = None
    medical_facility: str | None = None
    doctor_fee: float | None = None
    medicine_cost: float | None = None
    amount_payable: float | None = None


class ClaimInput(BaseModel):
    patient_name: str
    patient_address: str
    claim_item: str = "General Practitioner"
    date_of_treatment: str
    medical_facility: str
    claim_reason: str
    claim_amount: float
    invoice_hash: str | None = None
    extracted_invoice: InvoiceFields | None = None


class Citation(BaseModel):
    section: str
    title: str
    page: str
    excerpt: str


class ClaimDecision(BaseModel):
    status: str
    confidence: float = Field(ge=0, le=1)
    patient_name: str
    patient_address: str
    claim_item: str
    medical_facility: str
    date_of_treatment: str
    total_claim_amount: float
    executive_summary: str
    introduction: str
    claim_description: str
    document_verification: str
    document_summary: str
    conclusion: str
    reason_codes: list[str]
    flags: list[str]
    citations: list[Citation]
    cache_hit: bool = False
    pipeline_trace: dict = Field(default_factory=dict)
