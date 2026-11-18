# Project Interview Questions and Answers

## 1. What does the application do?

It is a document question-answering application based on retrieval-augmented generation (RAG). Users register, verify their email, upload text-based PDFs, and ask questions. The system retrieves relevant passages and returns an answer with document and page citations.

## 2. What is the architecture?

The React frontend calls a FastAPI backend. The backend extracts PDF text with pypdf, splits it into overlapping chunks, gets Gemini embeddings, and stores vectors and chunk metadata in ChromaDB. For a question, it retrieves relevant chunks and asks Gemini to answer from that context. SQLAlchemy stores users, document metadata, authentication tokens, and authentication rate-limit counters in SQLite locally or PostgreSQL in Docker Compose. SMTP sends verification and password-reset links.

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

The backend embeds the question and asks ChromaDB for up to five nearest chunks (`TOP_K=5`). If document IDs are supplied, retrieval filters by them. Before querying ChromaDB, the API checks that every requested document belongs to the authenticated user.

## 11. How is the final answer grounded?

Retrieved passages, document names, and page numbers are added to a prompt that instructs Gemini to answer only from the provided context. The API returns the answer, source passages, citations, similarity scores, and a sufficient-context flag.

## 12. What happens when there is not enough context?

If retrieval returns no chunks, the RAG service returns a standard not-enough-information answer, no citations, and a false context flag without calling the chat model. A question with no owned documents receives the same response from the API.

## 13. What does `top-k = 5` mean?

It is the maximum number of nearest chunks requested for a query, not a similarity threshold. Fewer than five may be returned when fewer eligible chunks exist.

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

## Quick architecture summary

```text
Registration → scrypt password hash + database account → SMTP verification link
Login → verified account → expiring bearer token → owner-checked document APIs

PDF → pypdf text/page extraction → 1,000-character chunks (150 overlap)
    → Gemini gemini-embedding-001 → ChromaDB vector storage

Question + owned document IDs → Gemini embedding → ChromaDB cosine search (top 5)
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
- `backend/app/services/document_store.py` — SQLAlchemy persistence, ownership, one-time tokens, and rate-limit counters
- `backend/app/core/config.py` — models, database, auth, SMTP, and RAG configuration
- `backend/tests/` — backend API, auth, SMTP, and RAG tests
- `frontend/src/App.jsx` — account flows, upload, document list, question form, answers, and citations
- `.github/workflows/ci.yml` — automated backend tests and frontend build
- `docker-compose.yml` — frontend/backend/PostgreSQL services and persistent volumes
