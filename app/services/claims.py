from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path

from app.services.cache import LRUCache, RedisSemanticCache, SemanticCache, stable_hash
from app.services.llm import ClaimLLMAgent
from app.services.rag import PolicyRAG
from app.services.schemas import Citation, ClaimDecision, ClaimInput


CACHE_SCHEMA_VERSION = "claims-pipeline-v19-high-confidence-fast-path"


class ClaimDecisionEngine:
    def __init__(self, policy_path: Path) -> None:
        self.rag = PolicyRAG(policy_path)
        self.agent = ClaimLLMAgent()
        self.exact_cache = LRUCache(max_size=256)
        self.semantic_cache = SemanticCache(threshold=0.94, max_size=128)
        self.redis_semantic_cache = RedisSemanticCache(
            namespace=f"claims:{CACHE_SCHEMA_VERSION}:{stable_hash(self.rag.policy_version)}",
            threshold=0.94,
            max_size=256,
        )
        self.self_rag_confidence_threshold = self._confidence_threshold()

    def decide(self, claim: ClaimInput) -> ClaimDecision:
        pipeline_trace = {
            "stage_1_ingestion_ocr": "completed before adjudication endpoint",
            "stage_2_prefill_form": "completed in browser before submission",
            "policy_version": self.rag.policy_version,
            "cache": {"exact": "miss", "semantic_memory": "miss", "semantic_redis": "disabled"},
        }
        cache_payload = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "policy_version": self.rag.policy_version,
            "claim": claim.model_dump(exclude={"extracted_invoice"}),
        }
        exact_key = stable_hash(cache_payload)
        exact = self.exact_cache.get(exact_key)
        if exact:
            exact.cache_hit = True
            exact.pipeline_trace["cache"] = {"exact": "hit"}
            return exact

        semantic_vector = self.rag.embed_query(self._claim_query(claim))
        semantic_guard = self._semantic_guard(claim)
        redis_semantic = self.redis_semantic_cache.get(semantic_vector)
        if redis_semantic and redis_semantic.get("guard") == semantic_guard:
            decision = ClaimDecision.model_validate(redis_semantic["decision"])
            decision.cache_hit = True
            decision.pipeline_trace["cache"] = {"semantic_redis": "hit"}
            return decision

        semantic = self.semantic_cache.get(semantic_vector)
        if semantic and semantic.get("guard") == semantic_guard:
            decision = semantic["decision"]
            decision.cache_hit = True
            decision.pipeline_trace["cache"] = {"semantic_memory": "hit"}
            return decision
        pipeline_trace["cache"]["semantic_redis"] = "miss" if self.redis_semantic_cache.enabled else "disabled"

        retrieval_plan = self.agent.plan_retrieval(claim)
        queries = self._build_retrieval_queries(retrieval_plan)
        pipeline_trace["stage_4_query_rewriting"] = {
            "route": retrieval_plan.route,
            "hyde": bool(retrieval_plan.hyde_document),
            "step_back_question": retrieval_plan.step_back_question,
            "multi_query_count": len(retrieval_plan.rewritten_queries[:8]),
            "metadata_filters": retrieval_plan.metadata_filters[:6],
            "query_count": len(queries),
        }

        docs = self.rag.retrieve(queries)
        pipeline_trace["stage_5_hybrid_retrieval"] = self.rag.last_retrieval_trace
        pipeline_trace["stage_6_cross_encoder_reranking"] = {
            "reranker": "FlashRank ms-marco-MiniLM-L-12-v2",
            "top_k": min(len(docs), 12),
        }
        flags = self._validate_claim(claim)
        flags.extend(retrieval_plan.document_checks[:8])

        citations = self._docs_to_citations(docs[:12])
        decision_draft = self.agent.decide(claim, citations, flags)
        self_rag_trace = {
            "threshold": self.self_rag_confidence_threshold,
            "initial_confidence": decision_draft.confidence,
            "mode": "skipped",
            "iterations": [],
        }
        if decision_draft.confidence < self.self_rag_confidence_threshold:
            self_rag_trace["mode"] = "triggered"
            citations_changed = False
            for iteration in range(1, 4):
                evidence_grade = self.agent.grade_evidence(claim, citations)
                evidence_sufficient = self._as_bool(evidence_grade.sufficient)
                self_rag_trace["iterations"].append(
                    {
                        "iteration": iteration,
                        "sufficient": evidence_sufficient,
                        "relevance_check": evidence_grade.relevance_check,
                        "grounding_check": evidence_grade.grounding_check,
                        "hallucination_risk": evidence_grade.hallucination_risk,
                        "contradiction_check": evidence_grade.contradiction_check,
                        "missing_questions": evidence_grade.missing_questions[:5],
                        "relevant_citation_indexes": evidence_grade.relevant_citation_indexes[:12],
                    }
                )
                if evidence_sufficient or not evidence_grade.missing_questions:
                    break

                follow_up_docs = self.rag.retrieve(evidence_grade.missing_questions[:5])
                follow_up_citations = self._docs_to_citations(follow_up_docs[:6])
                updated_citations = self._dedupe_citations(citations + follow_up_citations)
                citations_changed = citations_changed or len(updated_citations) > len(citations)
                citations = updated_citations

            if citations_changed:
                decision_draft = self.agent.decide(claim, citations, flags)
                self_rag_trace["redraft_confidence"] = decision_draft.confidence

        pipeline_trace["stage_7_self_rag_loop"] = self_rag_trace

        selected_citations = self._select_citations(citations, decision_draft.citation_indexes)
        verification_citations = self._dedupe_citations(selected_citations + citations)[:12]
        selected_citations = self._select_report_citations(selected_citations, verification_citations)
        final_status = decision_draft.status
        final_conclusion = decision_draft.conclusion
        final_flags = self._dedupe_strings(flags + decision_draft.flags)
        report = decision_draft

        if self_rag_trace["mode"] == "skipped":
            pipeline_trace["stage_7_final_grounding_verifier"] = "skipped for high-confidence fast path"
            pipeline_trace["stage_8_verified_report_writer"] = "skipped; initial decision report reused"
        else:
            final_verification = self.agent.verify_decision(claim, verification_citations, decision_draft)
            pipeline_trace["stage_7_final_grounding_verifier"] = final_verification.model_dump()
            final_flags = self._dedupe_strings(final_flags + final_verification.verifier_notes)
            verifier_grounded = self._as_bool(final_verification.grounded)
            verifier_blocked = (
                not verifier_grounded
                or final_verification.hallucination_risk == "high"
                or self._as_bool(final_verification.contradiction_found)
            )
            if verifier_blocked:
                final_status = final_verification.corrected_status
                final_conclusion = final_verification.corrected_conclusion
            elif final_verification.corrected_status != decision_draft.status:
                final_status = final_verification.corrected_status
                final_conclusion = self._report_conclusion_from_verifier(
                    claim=claim,
                    corrected_status=final_verification.corrected_status,
                    verifier_conclusion=final_verification.corrected_conclusion,
                )

            report = self.agent.write_report(
                claim=claim,
                citations=verification_citations,
                status=final_status,
            )
            pipeline_trace["stage_8_verified_report_writer"] = "completed"

        decision = ClaimDecision(
            status=final_status,
            confidence=decision_draft.confidence,
            patient_name=claim.patient_name,
            patient_address=claim.patient_address,
            claim_item=claim.claim_item,
            medical_facility=claim.medical_facility,
            date_of_treatment=claim.date_of_treatment,
            total_claim_amount=claim.claim_amount,
            executive_summary=report.executive_summary,
            introduction=report.introduction,
            claim_description=(
                f"{claim.patient_name} visited {claim.medical_facility} for {claim.claim_reason} "
                f"and the total claim amount is {claim.claim_amount:g}."
            ),
            document_verification=report.document_verification,
            document_summary=report.document_summary,
            conclusion=report.conclusion,
            reason_codes=decision_draft.reason_codes,
            flags=final_flags,
            citations=selected_citations,
            pipeline_trace=pipeline_trace,
        )

        self.exact_cache.set(exact_key, decision)
        self.semantic_cache.set(semantic_vector, {"guard": semantic_guard, "decision": decision})
        self.redis_semantic_cache.set(
            semantic_vector,
            {"guard": semantic_guard, "decision": decision.model_dump()},
        )
        return decision

    def _claim_query(self, claim: ClaimInput) -> str:
        return " ".join(
            [
                claim.patient_address,
                claim.claim_item,
                claim.date_of_treatment,
                claim.medical_facility,
                claim.claim_reason,
                str(claim.claim_amount),
            ]
        )

    def _semantic_guard(self, claim: ClaimInput) -> str:
        guard_payload = {
            "patient_name": claim.patient_name.strip().lower(),
            "date_of_treatment": claim.date_of_treatment.strip().lower(),
            "medical_facility": claim.medical_facility.strip().lower(),
            "claim_amount": round(float(claim.claim_amount), 2),
            "policy_version": self.rag.policy_version,
        }
        return stable_hash(guard_payload)

    def _confidence_threshold(self) -> float:
        try:
            threshold = float(os.getenv("SELF_RAG_CONFIDENCE_THRESHOLD", "0.75"))
        except ValueError:
            return 0.75
        return min(max(threshold, 0.0), 1.0)

    def _validate_claim(self, claim: ClaimInput) -> list[str]:
        flags: list[str] = []
        parsed_date = self._parse_date(claim.date_of_treatment)

        if parsed_date is None:
            flags.append("Treatment date could not be parsed.")
        elif parsed_date > date.today():
            flags.append("Treatment date is in the future.")
        elif parsed_date.year < 1900:
            flags.append("Treatment date is not realistic.")

        if not claim.patient_name:
            flags.append("Patient name is missing.")
        if not claim.medical_facility:
            flags.append("Medical facility is missing.")
        if claim.claim_amount <= 0:
            flags.append("Claim amount must be greater than zero.")
        if not claim.patient_address:
            flags.append("Patient address is missing.")
        elif not self._looks_like_uk_address(claim.patient_address):
            flags.append("Patient address is not clearly within the UK.")

        extracted = claim.extracted_invoice
        if extracted:
            dob = self._parse_date(extracted.date_of_birth or "")
            if dob and dob.year < 1900:
                flags.append("Invoice date of birth is not realistic.")
            if extracted.amount_payable is not None and abs(extracted.amount_payable - claim.claim_amount) > 1:
                flags.append("Entered amount does not match invoice amount.")

        return flags

    def _parse_date(self, value: str) -> date | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
        if match:
            try:
                return datetime.strptime(match.group(0), "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    def _looks_like_uk_address(self, value: str) -> bool:
        lower = value.lower()
        uk_terms = ["united kingdom", " uk", "england", "scotland", "wales", "northern ireland", "london"]
        return any(term in lower for term in uk_terms)

    def _as_bool(self, value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        return bool(value)

    def _short_excerpt(self, text: str) -> str:
        compact = " ".join(text.split())
        return compact[:320] + ("..." if len(compact) > 320 else "")

    def _build_retrieval_queries(self, retrieval_plan) -> list[str]:
        queries = [
            retrieval_plan.hyde_document,
            retrieval_plan.step_back_question,
            *retrieval_plan.rewritten_queries[:8],
            *retrieval_plan.required_policy_topics[:8],
        ]
        return [query for query in self._dedupe_strings(queries) if query]

    def _docs_to_citations(self, docs) -> list[Citation]:
        return [
            Citation(
                section=str(doc.metadata.get("section", "Policy")),
                title=str(doc.metadata.get("title", "Policy clause")),
                page=str(doc.metadata.get("page", "unknown")),
                excerpt=self._short_excerpt(doc.page_content),
            )
            for doc in docs
        ]

    def _select_citations(self, citations: list[Citation], indexes: list[int]) -> list[Citation]:
        selected = [citations[index] for index in indexes[:8] if 0 <= index < len(citations)]
        return selected[:5] if selected else citations[:5]

    def _select_report_citations(
        self,
        selected_citations: list[Citation],
        verification_citations: list[Citation],
    ) -> list[Citation]:
        evidence = self._dedupe_citations(selected_citations + verification_citations)
        policy_clauses = [
            citation
            for citation in evidence
            if citation.section.lower().startswith(("benefit", "exclusion", "pre-authorisation", "eligibility"))
        ]
        return self._dedupe_citations(policy_clauses + evidence)[:5]

    def _dedupe_citations(self, citations: list[Citation]) -> list[Citation]:
        seen: set[str] = set()
        deduped: list[Citation] = []
        for citation in citations:
            key = f"{citation.section}|{citation.page}|{citation.excerpt[:80]}"
            if key not in seen:
                seen.add(key)
                deduped.append(citation)
        return deduped

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                deduped.append(value)
        return deduped

    def _report_conclusion_from_verifier(
        self,
        claim: ClaimInput,
        corrected_status: str,
        verifier_conclusion: str,
    ) -> str:
        if corrected_status == "Approved":
            return (
                f"The claim submitted by {claim.patient_name} for {claim.claim_reason} treatment at "
                f"{claim.medical_facility} has been approved based on the submitted claim documents "
                "and the retrieved policy evidence."
            )
        if corrected_status == "Rejected":
            return (
                f"The claim submitted by {claim.patient_name} for {claim.claim_reason} treatment at "
                f"{claim.medical_facility} has been rejected based on the submitted claim documents "
                "and the retrieved policy evidence."
            )
        return verifier_conclusion
