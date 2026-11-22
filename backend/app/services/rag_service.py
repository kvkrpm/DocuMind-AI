import hashlib
import logging

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_core.prompts import ChatPromptTemplate
from app.core.config import settings
from app.services.hybrid_retrieval import (
    rank_bm25,
    reciprocal_rank_fusion,
    rerank,
)


logger = logging.getLogger(__name__)
_DENSE_TOP_K = 10
_BM25_TOP_K = 10
_RRF_CANDIDATE_LIMIT = 12
_FINAL_TOP_K = 4

# Provider switch
if settings.LLM_PROVIDER == "gemini":
    from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
    print(f"Using Gemini: {settings.GEMINI_MODEL}")
    embeddings_model = GoogleGenerativeAIEmbeddings(
        model=settings.GEMINI_EMBEDDING_MODEL,
        google_api_key=settings.GOOGLE_API_KEY
    )
    llm_model = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GOOGLE_API_KEY,
        convert_system_message_to_human=True
    )
else:
    from langchain_openai import OpenAIEmbeddings, ChatOpenAI
    embeddings_model = OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        openai_api_key=settings.OPENAI_API_KEY
    )
    llm_model = ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=0,
        openai_api_key=settings.OPENAI_API_KEY
    )

class RAGService:
    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=settings.CHROMA_PERSIST_DIR,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(
            name="workspace_docs",
            metadata={"hnsw:space": "cosine"}
        )
        self.embeddings = embeddings_model
        self.llm = llm_model

    def add_chunks(self, chunks):
        texts = [c["text"] for c in chunks]
        metadatas = [c["metadata"] for c in chunks]
        ids = [m["chunk_id"] for m in metadatas]
        vectors = self.embeddings.embed_documents(texts)
        self.collection.add(
            ids=ids,
            embeddings=vectors,
            documents=texts,
            metadatas=metadatas
        )
        return len(ids)

    def delete_doc(self, doc_id: str):
        self.collection.delete(where={"document_id": doc_id})

    @staticmethod
    def _record_id(document: str, metadata: dict) -> str:
        chunk_id = metadata.get("chunk_id")
        if chunk_id:
            return str(chunk_id)
        stable_content = (
            f"{metadata.get('document_id', '')}\0"
            f"{metadata.get('page_number', '')}\0{document}"
        )
        return hashlib.sha256(stable_content.encode("utf-8")).hexdigest()

    def query(self, question: str, doc_ids=None, k=5):
        del k
        if not doc_ids:
            return {
                "answer": "The documents do not contain enough information to answer this question.",
                "sources": [],
                "has_sufficient_context": False,
            }

        where_filter = {"document_id": {"$in": list(doc_ids)}}
        q_embedding = self.embeddings.embed_query(question)

        results = self.collection.query(
            query_embeddings=[q_embedding],
            n_results=_DENSE_TOP_K,
            where=where_filter
        )

        dense_docs = results.get("documents", [[]])[0]
        dense_metas = results.get("metadatas", [[]])[0]
        dense_ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])
        distances = distances[0] if distances else []

        dense_candidates = []
        for index, (doc, meta) in enumerate(zip(dense_docs, dense_metas)):
            distance = distances[index] if index < len(distances) else 0
            candidate_id = (
                dense_ids[index]
                if index < len(dense_ids)
                else self._record_id(doc, meta)
            )
            dense_candidates.append({
                "id": str(candidate_id),
                "text": doc,
                "metadata": meta,
                "dense_score": float(1 - distance),
            })

        workspace_chunks = self.collection.get(
            where=where_filter,
            include=["documents", "metadatas"],
        )
        corpus = []
        chunk_ids = workspace_chunks.get("ids", [])
        chunk_texts = workspace_chunks.get("documents", [])
        chunk_metas = workspace_chunks.get("metadatas", [])
        for index, (doc, meta) in enumerate(zip(chunk_texts, chunk_metas)):
            chunk_id = (
                chunk_ids[index]
                if index < len(chunk_ids)
                else self._record_id(doc, meta)
            )
            corpus.append({
                "id": str(chunk_id),
                "text": doc,
                "metadata": meta,
            })

        bm25_candidates = rank_bm25(question, corpus, limit=_BM25_TOP_K)
        fused_candidates = reciprocal_rank_fusion(
            dense_candidates[:_DENSE_TOP_K],
            bm25_candidates,
            limit=_RRF_CANDIDATE_LIMIT,
        )

        if not fused_candidates:
            return {
                "answer": "The documents do not contain enough information to answer this question.",
                "sources": [],
                "has_sufficient_context": False
            }

        try:
            ranked_candidates = rerank(question, fused_candidates)
        except Exception:
            logger.exception("FlashRank reranking failed; using RRF ranking")
            ranked_candidates = []
        if not ranked_candidates:
            ranked_candidates = fused_candidates
        selected_candidates = ranked_candidates[:_FINAL_TOP_K]

        context_str = ""
        sources = []
        for i, candidate in enumerate(selected_candidates):
            doc = candidate["text"]
            meta = candidate["metadata"]
            context_str += (
                f"\n[Source {i+1} | {meta['document_name']} - "
                f"Page {meta['page_number']}]\n{doc}\n"
            )
            sources.append({
                "document_name": meta["document_name"],
                "document_id": meta["document_id"],
                "page_number": meta["page_number"],
                "chunk_text": doc[:500],
                "score": float(
                    candidate.get("dense_score", candidate["rrf_score"])
                )
            })

        prompt = ChatPromptTemplate.from_template(
            """You are a document QA assistant. Answer ONLY based on the context below.
If the context does not contain enough information, explicitly say: "The documents do not contain enough information to answer this question."

Context:
{context}

Question: {question}

Requirements:
- Be accurate and context-aware
- Cite sources with document name and page number in your answer
- If not found, be clear about it

Answer:
"""
        )

        chain = prompt | self.llm
        resp = chain.invoke({"context": context_str, "question": question})

        answer = resp.text
        has_context = "do not contain enough information" not in answer.lower()

        return {
            "answer": answer,
            "sources": sources,
            "has_sufficient_context": has_context
        }

rag_service = RAGService()
