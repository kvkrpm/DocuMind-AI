# Project Interview Questions and Answers

## 1. What does the application do?

It is a document question-answering application based on retrieval-augmented generation (RAG). Users register, verify their email, upload text-based PDFs, and ask questions. The system retrieves relevant passages and returns an answer with document and page citations.

## 2. What is the architecture?

The React frontend calls a FastAPI backend. The backend extracts PDF text with pypdf, splits it into overlapping chunks, gets Gemini embeddings, and stores vectors and chunk metadata in ChromaDB. For a question, it runs workspace-scoped dense and BM25 retrieval, fuses the results with RRF, and reranks the candidates with FlashRank before asking Gemini to answer from the top passages. SQLAlchemy stores users, document metadata, authentication tokens, and authentication rate-limit counters in SQLite locally or PostgreSQL in Docker Compose. SMTP sends verification and password-reset links.

## 3. What is the end-to-end ingestion flow?

An authenticated user uploads a PDF. The backend checks its filename and size, saves it, extracts text and page numbers, splits the text page by page, and attaches document metadata to each chunk. Gemini embeds the chunks and ChromaDB indexes them. Once indexing succeeds, the relational database records the document metadata and its owner ID.

## 4. How is PDF text extracted and what happens to page numbers?

`pypdf` reads the PDF one page at a time. The original page number is attached to each extracted page and copied to every chunk made from it, enabling page citations. Scanned or image-only PDFs need OCR, which is not implemented.

## 5. How does chunking work?

LangChain's `RecursiveCharacterTextSplitter` splits each page using paragraph, newline, sentence, and space separators. The defaults are 1,000 characters per chunk and 150 characters of overlap. Chunks do not cross page boundaries, preserving simple page citation metadata.

## 6. Why use overlapping chunks?

Overlap preserves surrounding context when a relevant sentence falls near a chunk boundary. Without overlap, a sentence may be separated from information needed to understand it.

## 7. Which embedding model is configured?

The default is Google's `models/gemini-embedding-001`, configured by `GEMINI_EMBEDDING_MODEL`. The same embedding model is used for document chunks and user questions so they share a vector space.

## 8. Which answer-generation model is configured?

The default is `gemini-3.6-flash`, configured by `GEMINI_MODEL`. These are model identifiers, not package versions; availability and quota depend on the configured Google AI project.

## 9. Where are vectors stored?

ChromaDB stores the vectors, chunk text, and retrieval metadata in a persistent `workspace_docs` collection configured with cosine distance. Docker Compose stores it in a named volume.

## 10. How does retrieval work?

The backend embeds the question and asks ChromaDB for up to 10 nearest chunks. It also runs BM25 lexical search over the indexed chunk text, then fuses the rankings with Reciprocal Rank Fusion (RRF, `k=60`). The top 12 unique candidates are passed to FlashRank, which selects the top four. Dense search and the BM25 corpus are both restricted to the current workspace's allowed document IDs; the API checks ownership before querying.

## 11. How is the final answer grounded?

Retrieved passages, document names, and page numbers are added to a prompt that instructs Gemini to answer only from the provided context. The API returns the answer, source passages, citations, similarity scores, and a sufficient-context flag.

## 12. What happens when there is not enough context?

If retrieval returns no chunks, the RAG service returns a standard not-enough-information answer, no citations, and a false context flag without calling the chat model. A question with no owned documents receives the same response from the API.

## 13. What does top-4 mean?

It is the maximum number of reranked chunks supplied to the answer prompt and Sources panel, not a similarity threshold. Fewer than four may be returned when there are fewer eligible candidates.

## 14. What is the difference between ChromaDB and PostgreSQL/SQLite?

ChromaDB supports semantic nearest-neighbor search over vectors and chunk text. SQLite or PostgreSQL stores relational data such as users, document metadata, ownership, one-time auth tokens, and auth rate-limit counters. Uploaded PDF files are stored separately on disk or in a Docker volume.

## 15. What happens if Gemini's embedding quota is exceeded?

Indexing fails. The upload handler removes the uploaded PDF and maps Gemini's `RESOURCE_EXHAUSTED`/429 error to HTTP 429 with retry and quota guidance. The user may need to wait or review the Google AI project's quota and billing.

## 16. How is document deletion handled?

The API first checks that the document belongs to the authenticated user. It then deletes that document's vectors, uploaded PDF, and metadata. A document that is missing or belongs to another user returns 404.

## 17. How are users and passwords stored?

Users are stored in the relational database with a normalized unique email, a salted scrypt password hash, an email-verification state, and a token version. Plain-text passwords are not stored.

## 18. How does authentication work?

After email verification, login returns a signed bearer access token that expires after 12 hours by default. Protected API routes validate the token and load the account. Password reset increments the user's token version, invalidating previously issued sessions.

## 19. How do email verification and password recovery work?

The app sends one-time links through configured SMTP. Only a hash of each random token is stored in the database. Tokens expire after 30 minutes by default and are consumed once. Password recovery returns the same public response whether or not an account exists.

## 20. How is authentication abuse limited?

Registration is limited to five attempts per client IP per hour, login to ten per minute, and verification/recovery requests to five per hour by default. Counters live in the shared SQL database so they work across backend processes. Behind a reverse proxy, the server must trust forwarded client IPs only from that proxy.

## 21. How are users' documents isolated?

Each document row stores an owner ID. List and count operations filter by owner; delete checks ownership; query validates all requested document IDs against the current user's documents before making a vector search. Legacy documents without an owner stay hidden rather than being assigned to new users.

## 22. Which APIs support authentication and documents?

- `POST /api/auth/register` creates an unverified account and emails a verification link.
- `POST /api/auth/verify-email` verifies a one-time token; `/api/auth/verify-email/resend` requests another link.
- `POST /api/auth/login` issues a bearer token after email verification.
- `POST /api/auth/password/forgot` requests a reset link; `/api/auth/password/reset` consumes it.
- `GET /api/auth/me` returns the authenticated user.
- `GET /api/documents`, `POST /api/documents/upload`, `DELETE /api/documents/{doc_id}`, and `POST /api/query` require authentication.
- `GET /health` reports backend status and model configuration.

## 23. What is tested?

The backend `unittest` suite covers account registration/login, email verification, reset token expiry and one-time use, session invalidation, auth rate limits, user ownership boundaries, PDF upload/deletion, quota errors, retrieval filters/citations, and email formatting with SMTP mocked. It does not call live Gemini or SMTP. GitHub Actions runs the backend suite and a frontend production build. Docker/PostgreSQL startup still needs verification in the deployment environment.

## 24. What limitations and next steps remain?

Scanned-PDF OCR is intentionally out of scope; only PDFs with extractable text are supported. Direct Python dependencies are pinned, and CI checks the backend suite and frontend production build. Docker/PostgreSQL startup and live SMTP/Gemini calls have not been verified in this environment. Production deployment still requires HTTPS, a persistent random `AUTH_SECRET_KEY`, real SMTP and Gemini credentials, correct proxy configuration, backups, and operational/abuse-control review.

## 25. Why combine BM25 lexical search with dense embeddings?

Dense vector search finds semantically related passages even when the query and source use different wording, but semantic similarity can be weak for exact identifiers, acronyms, part numbers, and rare keywords. BM25 scores literal query-token overlap, making it useful for lookups such as `FIN-AP-204` or `API-429`. RRF combines the two ranked lists using rank contributions (`1 / (60 + rank)`), so their scores need not share a scale. This improves lexical recall without discarding semantic matches.

## 26. How does the two-stage search and TinyBERT reranker fit together?

Stage one independently retrieves up to 10 dense matches from workspace-filtered ChromaDB and up to 10 BM25 matches from that workspace's chunks. RRF merges duplicates and limits the candidate pool to 12. Stage two applies FlashRank's `ms-marco-TinyBERT-L-2-v2` cross-encoder to score the question-candidate pairs and chooses up to four passages for Gemini and the UI.

FlashRank runs through ONNX Runtime and is loaded lazily into a process-wide singleton. The measured local process RSS after model initialization was about 71 MiB; the dependency set has no PyTorch or Sentence-Transformers overhead. This is much lighter than a typical PyTorch cross-encoder and leaves substantially more room within a 512 MB cloud container budget, though total application memory still depends on ChromaDB, Python, and the deployment. If reranking fails, the service logs the error and returns the RRF ordering instead.

## 27. How do we measure retrieval quality, and what improved?

The offline dataset contains 12 Q&A cases with a mix of semantic questions and exact keyword, acronym, and ID lookups. The evaluation compares fixed dense rankings, hybrid BM25-plus-vector RRF, and hybrid retrieval with TinyBERT reranking. Recall@4 is reported as hit rate: whether at least one relevant chunk appears in the first four. MRR@4 averages the reciprocal rank of the first relevant chunk, with misses counted as zero.

In the recorded benchmark, dense-only retrieval has Recall@4 `0.917` and MRR@4 `0.292`; hybrid RRF keeps Recall@4 at `0.917` and raises MRR@4 to `0.708`; reranking reaches `1.000` on both metrics. This is an API-free benchmark: dense rankings are fixed fixtures rather than live Gemini/Chroma results, and latency measures local retrieval/ranking only, excluding embeddings and answer generation. FlashRank initialization is warmed before per-query timing.

## 28. What production deployment issues did we debug?

### Railway private networking and Nginx DNS

The frontend reaches the backend through Railway's private DNS name. Resolving a proxy upstream only when Nginx starts can leave a stale backend address after private-network changes. The Nginx template uses Railway's resolver (`[fd12::10]`) with a 30-second validity and a variable-backed `BACKEND_URL` so the target can be resolved dynamically. The configured private hostname must match the Railway backend service name.

### Multipart uploads and proxy timeouts

Multipart boundaries add bytes beyond the PDF itself, so an Nginx body limit equal to the backend's 10 MB PDF limit could reject an otherwise valid upload before FastAPI sees it. The frontend proxy allows 11 MB, leaving room for multipart overhead while the backend still enforces the file limit. Its API read and send timeouts are 300 seconds to accommodate larger PDF extraction and embedding requests; these proxy settings should be revisited alongside any backend upload-limit changes.

### SMTP port 587 and registration fallback

The documented Gmail path uses port 587 and STARTTLS. If the SMTP service is not configured or message delivery fails, the registration flow automatically verifies the new account and returns a notice so the user is not stranded by email delivery. This is an application-level fallback, not an automatic retry against a second SMTP port. `AUTO_VERIFY_EMAIL=true` can enable immediate verification explicitly, but that bypasses email ownership checks and should be used only when acceptable.

## Quick architecture summary

```text
Registration → scrypt password hash + database account → SMTP verification link
Login → verified account → expiring bearer token → owner-checked document APIs

PDF → pypdf text/page extraction → 1,000-character chunks (150 overlap)
    → Gemini gemini-embedding-001 → ChromaDB vector storage

Question + owned document IDs → Gemini embedding → ChromaDB dense search (top 10)
                              + workspace-scoped BM25 lexical search (top 10)
                              → RRF (k=60, up to 12 candidates)
                              → FlashRank TinyBERT reranking (top 4)
                              → grounded prompt → Gemini gemini-3.6-flash
                              → cited answer

Metadata/authentication → SQLite locally / PostgreSQL in Docker
```

## Source code map

- `backend/app/main.py` — FastAPI routes, authentication dependencies, and request orchestration
- `backend/app/services/auth_service.py` — password hashing and signed access/action tokens
- `backend/app/services/email_service.py` — SMTP delivery
- `backend/app/services/pdf_service.py` — PDF extraction, validation, and chunking
- `backend/app/services/rag_service.py` — Gemini models, ChromaDB indexing, retrieval, and answer generation
- `backend/app/services/hybrid_retrieval.py` — BM25 ranking, RRF fusion, and lazy FlashRank reranking
- `backend/eval/` — offline sample dataset, benchmark runner, and retrieval metrics report
- `backend/app/services/document_store.py` — SQLAlchemy persistence, ownership, one-time tokens, and rate-limit counters
- `backend/app/core/config.py` — models, database, auth, SMTP, and RAG configuration
- `backend/tests/` — backend API, auth, SMTP, and RAG tests
- `frontend/src/App.jsx` — account flows, upload, document list, question form, answers, and citations
- `.github/workflows/ci.yml` — automated backend tests and frontend build
- `docker-compose.yml` — frontend/backend/PostgreSQL services and persistent volumes
