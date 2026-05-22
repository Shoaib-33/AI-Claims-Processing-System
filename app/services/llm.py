from __future__ import annotations

import os
import json
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field

from app.services.schemas import Citation, ClaimInput


class LLMUnavailableError(RuntimeError):
    pass


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    route: Literal["health_claim", "cash_benefit", "exclusion_check", "human_review"]
    hyde_document: str = Field(description="A short hypothetical policy passage that would answer this claim.")
    step_back_question: str = Field(description="A broader policy question behind the claim.")
    rewritten_queries: list[str] = Field(default_factory=list)
    required_policy_topics: list[str] = Field(default_factory=list)
    metadata_filters: list[str] = Field(default_factory=list)
    document_checks: list[str] = Field(default_factory=list)


class EvidenceGrade(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sufficient: str = Field(description="Use 'true' or 'false'.")
    relevance_check: str
    grounding_check: str
    hallucination_risk: Literal["low", "medium", "high"]
    contradiction_check: str
    missing_questions: list[str] = Field(default_factory=list)
    relevant_citation_indexes: list[int] = Field(default_factory=list)


class DecisionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["Approved", "Rejected", "Needs Human Review"]
    confidence: float = Field(ge=0, le=1)
    executive_summary: str = Field(
        description="A report-ready paragraph summarizing the claim and decision basis.",
    )
    introduction: str = Field(
        description="A report-ready paragraph introducing the claim analysis and decision.",
    )
    document_verification: str = Field(
        description="A paragraph describing whether the submitted claim documents were verified."
    )
    document_summary: str = Field(
        description="A paragraph summarizing the submitted claim documents and material facts."
    )
    conclusion: str = Field(
        description="A comprehensive report paragraph stating the final status and evidence basis."
    )
    reason_codes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    citation_indexes: list[int] = Field(default_factory=list)


class FinalVerification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    grounded: str = Field(description="Use 'true' or 'false'.")
    hallucination_risk: Literal["low", "medium", "high"]
    contradiction_found: str = Field(description="Use 'true' or 'false'.")
    corrected_status: Literal["Approved", "Rejected", "Needs Human Review"]
    corrected_conclusion: str
    verifier_notes: list[str] = Field(default_factory=list)


class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    executive_summary: str = Field(description="A report-ready executive summary paragraph.")
    introduction: str = Field(description="A report-ready introduction paragraph.")
    document_verification: str = Field(description="A report-ready document verification paragraph.")
    document_summary: str = Field(description="A report-ready document summary paragraph.")
    conclusion: str = Field(description="A comprehensive final conclusion paragraph.")


def get_claims_llm() -> BaseChatModel:
    provider = os.getenv("CLAIMS_LLM_PROVIDER", "").strip().lower()

    if not provider:
        if os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        elif os.getenv("GROQ_API_KEY"):
            provider = "groq"

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("CLAIMS_LLM_MODEL", "gpt-4o-mini"),
            temperature=0,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key or api_key == "replace_with_your_groq_api_key":
            raise LLMUnavailableError("Set a real GROQ_API_KEY in .env before processing claims.")

        return ChatGroq(
            model=os.getenv("CLAIMS_LLM_MODEL", "llama-3.3-70b-versatile"),
            temperature=0,
        )

    raise LLMUnavailableError(
        "No claims LLM configured. Set OPENAI_API_KEY or GROQ_API_KEY, or set CLAIMS_LLM_PROVIDER."
    )


class ClaimLLMAgent:
    def __init__(self) -> None:
        self.llm = get_claims_llm()
        self.use_json_mode = "groq" in self.llm.__class__.__module__.lower()

    def plan_retrieval(self, claim: ClaimInput) -> RetrievalPlan:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an insurance claim retrieval router. Route the claim and rewrite it into precise "
                    "policy-search questions. Use HyDE, multi-query expansion, and step-back abstraction. "
                    "Do not decide the claim. Do not invent policy clauses. Ask for the evidence needed "
                    "to approve, reject, or route to human review. For Bupa health claims, always retrieve "
                    "evidence for: cover requirements, eligible treatment, outpatient consultations for acute "
                    "conditions, pre-authorisation, recognised consultants/facilities, outpatient medicines or "
                    "drug exclusions, and any relevant exclusions/exceptions. If uploaded claim documents "
                    "include an outpatient prescription or medicine charge, search specifically with the policy "
                    "terms 'outpatient drugs', 'drugs prescribed for outpatient treatment', and drug exclusions. "
                    "If the patient address, facility location, claim text, or uploaded documents indicate a "
                    "country outside the UK, search specifically for UK residency eligibility and overseas "
                    "treatment exclusions using the policy terms 'resident in the UK throughout the duration "
                    "of your cover' and 'overseas treatment outside of the UK'. "
                    "The policy may cover categories "
                    "such as acute conditions even when the exact diagnosis name is not listed.",
                ),
                (
                    "human",
                    "Claim:\n{claim}\n\nReturn a retrieval plan for a Bupa health insurance policy guide.\n"
                    "Return valid JSON matching this schema:\n{schema}",
                ),
            ]
        )
        chain = self._structured_chain(prompt, RetrievalPlan)
        return chain.invoke(
            {
                "claim": claim.model_dump_json(indent=2),
                "schema": self._schema_text(RetrievalPlan),
            }
        )

    def grade_evidence(self, claim: ClaimInput, citations: list[Citation]) -> EvidenceGrade:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a Self-RAG evidence grader. Decide whether the retrieved policy evidence is enough "
                    "to support a claim decision. Perform relevance check, grounding check, contradiction "
                    "check, and hallucination-risk assessment. Use only the supplied citations. If evidence "
                    "is missing or weak, write follow-up retrieval questions. For an acute outpatient claim, "
                    "evidence can be sufficient if the citations establish the general cover class and relevant "
                    "conditions/exclusions; do not require the policy to name the exact diagnosis if the claim "
                    "document identifies it as acute. Evidence rules: (1) Treat a claim reason or uploaded "
                    "document diagnosis that says acute as evidence that the claim is presented as acute. "
                    "(2) Policy citations do not need to repeat a patient-specific claim amount; invoice text "
                    "supports claimed amounts. (3) If the claim documents include billed outpatient medicines "
                    "or a prescription, return sufficient='false' unless the supplied citations include the "
                    "policy rule that covers or excludes outpatient drugs. A definition of common drugs alone "
                    "is not that rule. (4) If the claim documents include a billed diagnostic test, return "
                    "sufficient='false' unless a citation covers or excludes outpatient diagnostic tests. "
                    "(5) If the patient address, treatment facility, claim text, or uploaded documents show "
                    "a non-UK country, return sufficient='false' unless the citations include the UK residency "
                    "eligibility rule or the overseas treatment rule.",
                ),
                (
                    "human",
                    "Claim:\n{claim}\n\nRetrieved citations:\n{citations}\n\n"
                    "Return valid JSON matching this schema:\n{schema}",
                ),
            ]
        )
        chain = self._structured_chain(prompt, EvidenceGrade)
        return chain.invoke(
            {
                "claim": claim.model_dump_json(indent=2),
                "citations": _format_citations(citations),
                "schema": self._schema_text(EvidenceGrade),
            }
        )

    def decide(self, claim: ClaimInput, citations: list[Citation], validation_flags: list[str]) -> DecisionDraft:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an insurance claims adjudication assistant. Decide Approved, Rejected, or "
                    "Needs Human Review using only the provided policy citations and validation flags. "
                    "Reject only when the citations support rejection. Approve only when the citations support "
                    "coverage and required claim conditions. If evidence is incomplete or conflicting, choose "
                    "Needs Human Review. The claim JSON may include uploaded document text; treat invoice, "
                    "prescription, medical report, payment receipt, and pre-authorisation text as claim-document "
                    "evidence, while policy citations define the rules. Do not require the policy to name the "
                    "exact diagnosis when it covers the broader class, such as acute outpatient consultation. "
                    "Do not require patient-specific amounts to appear in policy citations; amounts come from "
                    "claim documents. Do not require separate medical-necessity proof unless the cited policy "
                    "rule requires it and the claim documents do not provide it. "
                    "Use facts in the claim documents to connect policy rules to the claim. Do not apply an "
                    "A&E, accident and emergency, urgent-care, or walk-in exclusion unless the claim documents "
                    "say the treatment followed one of those routes. If the documents show treatment outside "
                    "the UK and policy evidence excludes overseas treatment, apply that rule unless the "
                    "supplied evidence supports the stated exception. "
                    "If part of the amount is excluded, explain the payable/limited portion rather than failing "
                    "the whole claim automatically. Write report-ready prose for executive_summary, "
                    "introduction, document_verification, document_summary, and conclusion. The conclusion "
                    "must be a comprehensive paragraph of two to four sentences that states the claim status, "
                    "the submitted treatment facts, the document verification result, and the policy-evidence "
                    "basis. Do not return shorthand conclusions such as 'Claim approved' or 'Claim is valid'. "
                    "Keep each section suitable for the centered report UI. "
                    "Do not say a service or medicine is covered unless the supplied policy citations support "
                    "that statement. A definition of common drugs does not by itself prove an outpatient "
                    "medicine charge is covered. If a cited exclusion conflicts with a claimed item, state that limitation. "
                    "The report fields may be shown directly to the claimant on high-confidence decisions. "
                    "Keep them customer-facing: do not mention Self-RAG, grounding, hallucination checks, "
                    "internal verification stages, proposed decisions, citation indexes such as [3], or policy "
                    "chunk numbers. If policy evidence supports one part of a bill but excludes another, "
                    "name the benefit or exclusion plainly and avoid repeating the same human-review sentence. "
                    "Do not reveal hidden reasoning.",
                ),
                (
                    "human",
                    "Claim:\n{claim}\n\nValidation flags:\n{flags}\n\nPolicy citations:\n{citations}\n\n"
                    "Return valid JSON matching this schema:\n{schema}",
                ),
            ]
        )
        chain = self._structured_chain(prompt, DecisionDraft)
        return chain.invoke(
            {
                "claim": claim.model_dump_json(indent=2),
                "flags": "\n".join(f"- {flag}" for flag in validation_flags) or "None",
                "citations": _format_citations(citations),
                "schema": self._schema_text(DecisionDraft),
            }
        )

    def verify_decision(
        self,
        claim: ClaimInput,
        citations: list[Citation],
        decision: DecisionDraft,
    ) -> FinalVerification:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are the final Self-RAG verifier. Check whether the proposed claim decision is grounded "
                    "in the supplied claim documents and policy citations. Verification rules: "
                    "(1) Claim-document text proves patient facts such as invoice amount, treatment date, "
                    "diagnosis, medical report, prescription, payment, and pre-authorisation. Policy citations "
                    "prove coverage rules. Never require a patient-specific claim amount to appear in policy text. "
                    "(2) A policy citation for outpatient consultations for acute conditions can ground an "
                    "acute outpatient consultation even if it does not name the exact diagnosis. "
                    "(3) Treat a claim reason or uploaded diagnosis that says acute as evidence that the claim "
                    "is presented as acute. Never say the documents omit acute status when that text is present. "
                    "(4) Do not require separate medical-necessity proof unless the cited policy rule requires it. "
                    "(5) A common-drugs definition does not by itself prove an outpatient prescription charge "
                    "is covered; use a citation that covers or excludes outpatient drugs. Verify against all "
                    "supplied citations, not only the first broad policy citation. "
                    "(6) Do not apply A&E, urgent-care, or walk-in exclusions unless claim documents identify "
                    "that treatment route. If claim documents show treatment outside the UK and policy evidence "
                    "contains the overseas-treatment exclusion, use that rule unless exception evidence is supplied. "
                    "If the decision is not grounded, correct it to Needs Human Review. When you write corrected_conclusion, use a "
                    "report-ready paragraph rather than a one-line status.",
                ),
                (
                    "human",
                    "Claim:\n{claim}\n\nProposed decision:\n{decision}\n\nPolicy citations:\n{citations}\n\n"
                    "Return valid JSON matching this schema:\n{schema}",
                ),
            ]
        )
        chain = self._structured_chain(prompt, FinalVerification)
        return chain.invoke(
            {
                "claim": claim.model_dump_json(indent=2),
                "decision": decision.model_dump_json(indent=2),
                "citations": _format_citations(citations),
                "schema": self._schema_text(FinalVerification),
            }
        )

    def write_report(
        self,
        claim: ClaimInput,
        citations: list[Citation],
        status: str,
    ) -> ReportDraft:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You write the visible insurance claim report after adjudication and Self-RAG "
                    "verification. The verified status is authoritative. Use the claim-document text for "
                    "invoice, prescription, report, payment, and pre-authorisation facts. Use the policy "
                    "evidence for coverage rules. The executive summary, introduction, verification, "
                    "summary, and conclusion must agree with the verified status. Do not say that an item "
                    "is covered when the supplied policy citations do not support it. Do not require an exact "
                    "diagnosis name in policy text when a citation covers the broader treatment class such as "
                    "acute outpatient consultations. If the claim or uploaded documents state that the "
                    "condition is acute, do not say that the documents omit that fact. A common-drugs "
                    "definition does not by itself make an outpatient prescription charge covered. If a cited exclusion affects a billed item, explain that "
                    "policy limitation clearly. The conclusion must be two to four sentences and be suitable "
                    "for a formal centered report UI. Policy citations do not need to include the patient "
                    "specific claim amount; that amount belongs in the claim documents. Keep the report "
                    "customer-facing: do not mention Self-RAG, grounding, hallucination checks, internal "
                    "verification stages, proposed decisions, citation indexes such as [3], or policy chunk "
                    "numbers. If you need to mention evidence, use natural policy names such as outpatient "
                    "consultation benefit or outpatient drugs exclusion. When policy evidence supports one "
                    "part of the bill but excludes another billed item, explain the covered and excluded "
                    "parts plainly and say human review is needed only to determine the payable amount or "
                    "final handling of the mixed claim. If an outpatient drugs exclusion is supplied and "
                    "the bill includes prescribed outpatient medicines, say that the medicine charge is "
                    "excluded or not covered under that exclusion; do not soften it to 'may be limited'. "
                    "Do not say 'policy citations' in the customer report; say 'policy evidence', 'policy "
                    "terms', or name the relevant benefit or exclusion. Do not repeat the same human-review "
                    "sentence within a section. Describe document review as completeness and consistency "
                    "checking unless the document text itself proves authenticity. Do not mention A&E, "
                    "urgent-care, or walk-in exclusions unless the claim documents state that treatment route. "
                    "If the documents show treatment outside the UK and the supplied evidence contains the "
                    "overseas-treatment exclusion or UK residency eligibility rule, explain that directly. "
                    "Do not reveal hidden reasoning.",
                ),
                (
                    "human",
                    "Verified status: {status}\n\nClaim:\n{claim}\n\nPolicy evidence:\n{citations}\n\n"
                    "Return valid JSON matching this schema:\n{schema}",
                ),
            ]
        )
        chain = self._structured_chain(prompt, ReportDraft)
        return chain.invoke(
            {
                "status": status,
                "claim": claim.model_dump_json(indent=2),
                "citations": _format_citations(citations, include_indexes=False),
                "schema": self._schema_text(ReportDraft),
            }
        )

    def _schema_text(self, schema: type[BaseModel]) -> str:
        return json.dumps(schema.model_json_schema(), indent=2)

    def _structured_chain(self, prompt: ChatPromptTemplate, schema: type[BaseModel]):
        if self.use_json_mode:
            # Groq function-calling can reject a response before Pydantic can ignore
            # harmless extra keys. JSON mode keeps the schema enforcement local.
            parser = PydanticOutputParser(pydantic_object=schema)
            return prompt | self.llm.bind(response_format={"type": "json_object"}) | parser
        return prompt | self.llm.with_structured_output(schema)


def _format_citations(citations: list[Citation], include_indexes: bool = True) -> str:
    lines: list[str] = []
    for index, citation in enumerate(citations):
        prefix = f"[{index}] " if include_indexes else ""
        lines.append(f"{prefix}{citation.section} - {citation.title}, page {citation.page}\n{citation.excerpt}")
    return "\n\n".join(lines)
