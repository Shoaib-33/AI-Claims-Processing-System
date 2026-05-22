from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.services.claims import ClaimDecisionEngine
from app.services.cache import LRUCache
from app.services.llm import LLMUnavailableError
from app.services.ocr import InvoiceExtractor
from app.services.schemas import ClaimInput


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "frontend"
POLICY_PATH = BASE_DIR / "data" / "policies" / "bupa.pdf"

app = FastAPI(title="AI Claims Processing System", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

invoice_extractor = InvoiceExtractor()
invoice_fields_cache = LRUCache(max_size=128)
decision_engine: ClaimDecisionEngine | None = None


def get_decision_engine() -> ClaimDecisionEngine:
    global decision_engine
    if decision_engine is None:
        try:
            decision_engine = ClaimDecisionEngine(policy_path=POLICY_PATH)
        except LLMUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return decision_engine


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/extract-invoice")
async def extract_invoice(invoice: Annotated[UploadFile, File(...)]) -> dict:
    content = await invoice.read()
    if not content:
        raise HTTPException(status_code=400, detail="Invoice file is empty")

    file_hash = hashlib.sha256(content).hexdigest()
    extracted = invoice_extractor.extract(content, invoice.filename or "invoice.pdf")
    invoice_fields_cache.set(file_hash, extracted)
    return {"invoice_hash": file_hash, "fields": extracted.model_dump()}


@app.post("/api/process-claim")
async def process_claim(
    patient_name: Annotated[str, Form()],
    patient_address: Annotated[str, Form()],
    date_of_treatment: Annotated[str, Form()],
    medical_facility: Annotated[str, Form()],
    claim_reason: Annotated[str, Form()],
    claim_amount: Annotated[float, Form()],
    claim_item: Annotated[str, Form()] = "General Practitioner",
    invoice_hash: Annotated[str | None, Form()] = None,
    invoice: Annotated[UploadFile | None, File()] = None,
) -> dict:
    upload_hash = invoice_hash
    extracted_fields = None

    if invoice is not None:
        content = await invoice.read()
        if content:
            upload_hash = hashlib.sha256(content).hexdigest()
            extracted_fields = invoice_fields_cache.get(upload_hash)
            if extracted_fields is None:
                extracted_fields = invoice_extractor.extract(content, invoice.filename or "invoice.pdf")
                invoice_fields_cache.set(upload_hash, extracted_fields)
    elif upload_hash:
        extracted_fields = invoice_fields_cache.get(upload_hash)

    claim = ClaimInput(
        patient_name=patient_name.strip(),
        patient_address=patient_address.strip(),
        claim_item=claim_item.strip(),
        date_of_treatment=date_of_treatment.strip(),
        medical_facility=medical_facility.strip(),
        claim_reason=claim_reason.strip(),
        claim_amount=claim_amount,
        invoice_hash=upload_hash,
        extracted_invoice=extracted_fields,
    )

    return get_decision_engine().decide(claim).model_dump()
