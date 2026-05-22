from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter


_POLICY_DOCUMENT_CACHE: dict[str, list[Document]] = {}


class PolicyRAG:
    def __init__(self, policy_path: Path) -> None:
        self.policy_path = policy_path
        self.policy_version = self._policy_version()
        self.last_retrieval_trace: list[dict[str, Any]] = []
        self.documents = self._load_documents()
        self.embeddings = None
        self.reranker = None
        self.bm25_retriever, self.dense_retriever = self._build_retrievers()

    def _policy_version(self) -> str:
        stat = self.policy_path.stat()
        return f"{self.policy_path.name}:{stat.st_size}:{int(stat.st_mtime)}"

    def _load_documents(self) -> list[Document]:
        cache_key = self.policy_version
        if cache_key in _POLICY_DOCUMENT_CACHE:
            return _POLICY_DOCUMENT_CACHE[cache_key]

        docs = self._load_pdf_documents() if self.policy_path.suffix.lower() == ".pdf" else self._load_text_documents()

        splitter = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=150)
        split_docs = splitter.split_documents(docs)
        _POLICY_DOCUMENT_CACHE[cache_key] = split_docs
        return split_docs

    def _load_text_documents(self) -> list[Document]:
        raw = self.policy_path.read_text(encoding="utf-8")
        docs: list[Document] = []
        section = "Policy"
        title = "Bupa Health policy"
        page = "unknown"
        buffer: list[str] = []

        def flush() -> None:
            if buffer:
                docs.append(
                    Document(
                        page_content="\n".join(buffer).strip(),
                        metadata={"section": section, "title": title, "page": page, "source": str(self.policy_path)},
                    )
                )

        for line in raw.splitlines():
            if line.startswith("## "):
                flush()
                buffer = []
                heading = line[3:].strip()
                pieces = [part.strip() for part in heading.split("|")]
                section = pieces[0]
                title = pieces[1] if len(pieces) > 1 else pieces[0]
                page = pieces[2].replace("page", "").strip() if len(pieces) > 2 else "unknown"
            elif line.strip():
                buffer.append(line.strip())
        flush()
        return docs

    def _load_pdf_documents(self) -> list[Document]:
        docs: list[Document] = []
        with fitz.open(self.policy_path) as pdf:
            for page_index, page in enumerate(pdf, start=1):
                text = page.get_text("text")
                if not text.strip():
                    continue
                for block_index, block in enumerate(self._split_policy_page(text), start=1):
                    section, title = self._infer_section_title(block, page_index, block_index)
                    docs.append(
                        Document(
                            page_content=block,
                            metadata={
                                "section": section,
                                "title": title,
                                "page": str(page_index),
                                "source": str(self.policy_path),
                            },
                        )
                    )
        if not docs:
            raise ValueError(f"No text could be extracted from policy PDF: {self.policy_path}")
        return docs

    def _split_policy_page(self, text: str) -> list[str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        blocks: list[list[str]] = []
        current: list[str] = []

        for line in lines:
            is_heading = self._looks_like_heading(line)
            if is_heading and current:
                blocks.append(current)
                current = [line]
            else:
                current.append(line)

        if current:
            blocks.append(current)

        merged: list[str] = []
        buffer = ""
        for block in blocks:
            candidate = " ".join(block)
            if len(buffer) < 260:
                buffer = f"{buffer} {candidate}".strip()
            else:
                merged.append(buffer)
                buffer = candidate
        if buffer:
            merged.append(buffer)
        return merged

    def _looks_like_heading(self, line: str) -> bool:
        if len(line) > 90:
            return False
        lower = line.lower()
        heading_terms = [
            "benefit",
            "exclusion",
            "what is covered",
            "what isn't covered",
            "what isnt covered",
            "eligibility",
            "pre-authorisation",
            "pre-authorization",
            "claim",
            "complain",
            "definition",
            "privacy",
        ]
        numbered = line[:2].strip(".").isdigit() or line[:3].strip(".").isdigit()
        title_case = line[:1].isupper() and sum(1 for char in line if char.isalpha()) > 4
        return numbered or any(term in lower for term in heading_terms) or (title_case and len(line.split()) <= 7)

    def _infer_section_title(self, block: str, page_index: int, block_index: int) -> tuple[str, str]:
        first = block.split(". ")[0].strip()
        first = first[:80] if first else f"Policy page {page_index}"
        lower = block.lower()
        numbered_exclusion = self._extract_numbered_exclusion(block)

        if numbered_exclusion:
            section = numbered_exclusion
        elif "exclusion" in lower:
            section = self._extract_labeled_section(block, "Exclusion") or f"Exclusion evidence p{page_index}.{block_index}"
        elif "benefit" in lower:
            section = self._extract_labeled_section(block, "Benefit") or f"Benefit evidence p{page_index}.{block_index}"
        elif "pre-authorisation" in lower or "pre-authorization" in lower:
            section = "Pre-authorisation"
        elif "eligib" in lower or "resident in the uk" in lower:
            section = "Eligibility"
        else:
            section = f"Policy p{page_index}.{block_index}"

        return section, first

    def _extract_labeled_section(self, block: str, label: str) -> str | None:
        import re

        match = re.search(rf"\b({label})\s+([A-Z]?[A-Z0-9.]+)?", block, flags=re.IGNORECASE)
        if not match:
            return None
        suffix = (match.group(2) or "").strip()
        return f"{label} {suffix}".strip()

    def _extract_numbered_exclusion(self, block: str) -> str | None:
        import re

        lower = block.lower()
        if "not covered" not in lower and "isn" not in lower and "arent covered" not in lower:
            return None
        match = re.match(r"\s*(\d{1,2})\s+[A-Z]", block)
        return f"Exclusion {match.group(1)}" if match else None

    def _build_retrievers(self):
        bm25 = BM25Retriever.from_documents(self.documents)
        bm25.k = 10

        try:
            from langchain_community.embeddings import HuggingFaceEmbeddings

            self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
            vector_store = FAISS.from_documents(self.documents, self.embeddings)
            dense = vector_store.as_retriever(search_kwargs={"k": 10})
            return bm25, dense
        except Exception:
            return bm25, None

    def retrieve(self, queries: list[str]) -> list[Document]:
        self.last_retrieval_trace = []
        seen: set[str] = set()
        candidates: list[Document] = []
        anchors: list[Document] = []
        for query in queries:
            query_docs = self._hybrid_search(query)
            for doc in query_docs[:2]:
                key = f"{doc.metadata.get('section')}::{doc.page_content[:120]}"
                if key not in seen:
                    seen.add(key)
                    anchors.append(doc)
                    candidates.append(doc)
            for doc in query_docs:
                key = f"{doc.metadata.get('section')}::{doc.page_content[:120]}"
                if key not in seen:
                    seen.add(key)
                    candidates.append(doc)

        reranked = self._dedupe_documents(anchors + self._rerank(" ".join(queries), candidates))[:12]
        self.last_retrieval_trace.append(
            {
                "stage": "reranking",
                "reranker": "flashrank/ms-marco-MiniLM-L-12-v2",
                "candidate_count": len(candidates),
                "per_query_anchor_count": len(anchors),
                "selected_count": len(reranked),
            }
        )
        return reranked

    def _hybrid_search(self, query: str) -> list[Document]:
        bm25_ranked = self.bm25_retriever.invoke(query)
        ranked_lists = [bm25_ranked]
        dense_ranked = []
        if self.dense_retriever is not None:
            dense_ranked = self.dense_retriever.invoke(query)
            ranked_lists.append(dense_ranked)

        scores: dict[str, float] = {}
        docs_by_key: dict[str, Document] = {}
        for ranked in ranked_lists:
            for rank, doc in enumerate(ranked, start=1):
                key = f"{doc.metadata.get('section')}::{doc.page_content[:120]}"
                docs_by_key[key] = doc
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)

        fused = [docs_by_key[key] for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
        self.last_retrieval_trace.append(
            {
                "stage": "hybrid_retrieval",
                "query": query,
                "dense_results": len(dense_ranked),
                "sparse_results": len(bm25_ranked),
                "fusion": "reciprocal_rank_fusion",
                "fused_results": len(fused),
            }
        )
        return fused

    def embed_query(self, query: str) -> list[float]:
        if self.embeddings is not None:
            return self.embeddings.embed_query(query)
        import hashlib

        vector = [0.0] * 384
        for word in query.lower().split():
            slot = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16) % len(vector)
            vector[slot] += 1.0
        return vector

    def _rerank(self, query: str, docs: list[Document]) -> list[Document]:
        if not docs:
            return []

        try:
            from flashrank import Ranker, RerankRequest

            if self.reranker is None:
                self.reranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2")
            passages = [
                {"id": str(index), "text": doc.page_content, "meta": doc.metadata}
                for index, doc in enumerate(docs)
            ]
            results = self.reranker.rerank(RerankRequest(query=query, passages=passages))
            ordered = [docs[int(result["id"])] for result in results]
            return ordered
        except Exception:
            keywords = {word.lower() for word in query.split() if len(word) > 3}

            def score(doc: Document) -> int:
                text = doc.page_content.lower()
                metadata = " ".join(str(v).lower() for v in doc.metadata.values())
                return sum(1 for word in keywords if word in text or word in metadata)

            return sorted(docs, key=score, reverse=True)

    def _dedupe_documents(self, docs: list[Document]) -> list[Document]:
        seen: set[str] = set()
        deduped: list[Document] = []
        for doc in docs:
            key = f"{doc.metadata.get('section')}::{doc.page_content[:120]}"
            if key not in seen:
                seen.add(key)
                deduped.append(doc)
        return deduped
