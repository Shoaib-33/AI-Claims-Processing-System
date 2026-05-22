from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

import fitz
from langchain_core.prompts import ChatPromptTemplate
from PIL import Image

from app.services.llm import LLMUnavailableError, get_claims_llm
from app.services.schemas import InvoiceFields


class InvoiceExtractor:
    def extract(self, content: bytes, filename: str) -> InvoiceFields:
        is_pdf = filename.lower().endswith(".pdf")
        fast_text = self._extract_pdf_text(content) if is_pdf else ""
        fast_fields = self._parse_invoice_text(fast_text)
        if self._has_prefill_fields(fast_fields):
            return fast_fields

        docling_text = self._extract_docling_text(content, filename)
        docling_fields = self._parse_invoice_text(docling_text)
        text = docling_text if len(docling_text.strip()) >= len(fast_text.strip()) else fast_text
        fallback = self._merge_fields(primary=docling_fields, fallback=fast_fields)
        if self._has_prefill_fields(fallback):
            return fallback

        if len(text.strip()) < 30:
            text = self._ocr_bytes(content, filename)
            fallback = self._merge_fields(primary=self._parse_invoice_text(text), fallback=fallback)
            if self._has_prefill_fields(fallback):
                return fallback

        structured = self._extract_fields_with_langchain(text)
        return self._merge_fields(primary=structured, fallback=fallback)

    def _extract_docling_text(self, content: bytes, filename: str) -> str:
        suffix = Path(filename).suffix or ".pdf"
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except Exception:
            return ""

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            try:
                if suffix.lower() == ".pdf":
                    options = PdfPipelineOptions()
                    options.do_ocr = False
                    options.do_table_structure = True
                    converter = DocumentConverter(
                        format_options={
                            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                        }
                    )
                else:
                    converter = DocumentConverter()

                result = converter.convert(tmp_path)
                return result.document.export_to_markdown()
            finally:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            return ""

    def _extract_fields_with_langchain(self, text: str) -> InvoiceFields | None:
        if len(text.strip()) < 30:
            return None

        try:
            llm = get_claims_llm()
        except LLMUnavailableError:
            return None
        except Exception:
            return None

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "Extract hospital invoice and claim-package fields into the provided schema. "
                    "Use only the document text. If a field is absent, return null. "
                    "Normalize dates only when clear. Keep raw_text as the original document text.",
                ),
                ("human", "Document text:\n{text}"),
            ]
        )

        try:
            chain = prompt | llm.with_structured_output(InvoiceFields)
            extracted = chain.invoke({"text": text[:12000]})
            extracted.raw_text = text
            return extracted
        except Exception:
            return None

    def _extract_pdf_text(self, content: bytes) -> str:
        try:
            with fitz.open(stream=content, filetype="pdf") as doc:
                return "\n".join(page.get_text("text") for page in doc)
        except Exception:
            return ""

    def _ocr_bytes(self, content: bytes, filename: str) -> str:
        try:
            import pytesseract
        except Exception:
            return ""

        try:
            if filename.lower().endswith(".pdf"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    pdf = fitz.open(stream=content, filetype="pdf")
                    parts: list[str] = []
                    for page in pdf:
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                        path = Path(tmpdir) / f"page-{page.number}.png"
                        pix.save(path)
                        parts.append(pytesseract.image_to_string(Image.open(path)))
                    return "\n".join(parts)

            return pytesseract.image_to_string(Image.open(io.BytesIO(content)))
        except Exception:
            return ""

    def _parse_invoice_text(self, text: str) -> InvoiceFields:
        clean = re.sub(r"[ \t]+", " ", text)

        def first(patterns: list[str]) -> str | None:
            for pattern in patterns:
                match = re.search(pattern, clean, flags=re.IGNORECASE)
                if match:
                    return match.group(1).strip(" :-\n\t")
            return None

        def money(patterns: list[str]) -> float | None:
            value = first(patterns)
            if value is None:
                return None
            amount_match = re.search(r"[\d,.]+", value)
            return float(amount_match.group(0).replace(",", "")) if amount_match else None

        facility = first([r"##\s*([A-Z][A-Z ]+HOSPITALS?)", r"^\s*([A-Z][A-Z ]+HOSPITALS?)", r"Medical Facility[:\s]+([^\n]+)"])
        patient_name = first([r"(?:Patient )?Name[:\s-]+(.+?)(?=\s+-?\s*Date of Birth|\s+DOB|\n|$)"])
        dob = first([r"Date of Birth[:\s-]+([0-9/\-]+)", r"DOB[:\s-]+([0-9/\-]+)"])
        address = first([r"Address[:\s-]+(.+?)(?=\s+-?\s*Phone|\n|$)"])
        phone = first([r"Phone Number[:\s-]+(.+?)(?=\s+-?\s*Policy Number|\s+Service Details|\n|$)", r"Phone[:\s-]+(.+?)(?=\s+-?\s*Policy Number|\s+Service Details|\n|$)"])
        date_of_service = first([r"Date of Service[:\s-]+([0-9/\-]+)", r"Date of Treatment[:\s-]+([0-9/\-]+)"])
        diagnosis = first([r"Diagnosis[:\s-]+(.+?)(?=\s+-?\s*Treatment Type|\s+Prescribed Medicines|\s+Details:|\n|$)", r"Claim Reason[:\s-]+([^\n]+)"])
        total = money([r"Amount payable[:\s=-]+([^\n]+)", r"Total charge[:\s=-]+([^\n]+)", r"Total Claim Amount[:\s=-]+([^\n]+)"])
        doctor_fee = money([r"Doctor'?s (?:consultation )?fee\s*[-:=]\s*([0-9,.]+)", r"Doctor'?s fee[:\s=-]+(.+?)(?=\s+Medicines?|\n|$)"])
        medicine_cost = money([r"Prescribed medicines\s*[-:=]\s*([0-9,.]+)", r"Medicines?\s*[-:=]\s*([0-9,.]+)", r"Medicine cost\s*[-:=]\s*([0-9,.]+)"])

        return InvoiceFields(
            raw_text=text,
            patient_name=patient_name,
            date_of_birth=dob,
            address=address,
            phone=phone,
            date_of_treatment=date_of_service,
            diagnosis=diagnosis,
            medical_facility=facility,
            doctor_fee=doctor_fee,
            medicine_cost=medicine_cost,
            amount_payable=total,
        )

    def _merge_fields(self, primary: InvoiceFields | None, fallback: InvoiceFields) -> InvoiceFields:
        if primary is None:
            return fallback

        data = primary.model_dump()
        fallback_data = fallback.model_dump()
        for key, value in data.items():
            if value in (None, "") and fallback_data.get(key) not in (None, ""):
                data[key] = fallback_data[key]

        # Payable amount is the correct claim amount when both total charge and discounted amount exist.
        if fallback.amount_payable is not None and re.search(r"amount payable", fallback.raw_text, flags=re.IGNORECASE):
            data["amount_payable"] = fallback.amount_payable

        # Regex fallback is often more precise for flattened Docling invoice lines.
        for key in ("patient_name", "date_of_birth", "address", "phone", "date_of_treatment", "diagnosis", "medical_facility"):
            if fallback_data.get(key) not in (None, ""):
                data[key] = fallback_data[key]

        if not data.get("raw_text"):
            data["raw_text"] = fallback.raw_text
        return InvoiceFields(**data)

    def _has_prefill_fields(self, fields: InvoiceFields) -> bool:
        required = (
            fields.patient_name,
            fields.address,
            fields.date_of_treatment,
            fields.diagnosis,
            fields.medical_facility,
            fields.amount_payable,
        )
        return all(value not in (None, "") for value in required)
