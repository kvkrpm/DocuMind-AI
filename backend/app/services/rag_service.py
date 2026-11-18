import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_core.prompts import ChatPromptTemplate
from app.core.config import settings

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

    def query(self, question: str, doc_ids=None, k=5):
        q_embedding = self.embeddings.embed_query(question)
        where_filter = {"document_id": {"$in": doc_ids}} if doc_ids else None

        results = self.collection.query(
            query_embeddings=[q_embedding],
            n_results=k,
            where=where_filter
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0] if results.get("distances") else [0]*len(docs)

        if not docs:
            return {
                "answer": "The documents do not contain enough information to answer this question.",
                "sources": [],
                "has_sufficient_context": False
            }

        context_str = ""
        sources = []
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
            context_str += f"\n[Source {i+1} | {meta['document_name']} - Page {meta['page_number']}]\n{doc}\n"
            sources.append({
                "document_name": meta["document_name"],
                "document_id": meta["document_id"],
                "page_number": meta["page_number"],
                "chunk_text": doc[:500],
                "score": float(1 - dist)
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
