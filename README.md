# DocuMind AI

Upload text-based PDFs, ask questions about their contents, and get answers with document and page citations.

## Live Demo

<https://documind-ai-production-dfd1e8ac.up.railway.app/>

## Requirements

- Python 3.10 or later
- Node.js 20.19+ or 22.12+ (for local frontend development)
- A Google AI Studio API key for Gemini embeddings and answers
- Docker Compose (optional)

## Configure

Create a `.env` file in the project root using `.env.example` as a template. Set `GOOGLE_API_KEY`, replace `POSTGRES_PASSWORD` with a strong URL-safe password, and set `AUTH_SECRET_KEY` to a random value of at least 32 characters. Do not commit `.env`.

For local backend development, copy the required settings into `backend/.env`. Set `AUTH_SECRET_KEY` there as well; the backend uses SQLite by default, and `DATABASE_URL` can select another database. If no auth key is set in local development, the app generates a temporary key and existing sessions will stop working after a restart. The root `.env` is used by Docker Compose.

Generate secrets using Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the first generated value for `AUTH_SECRET_KEY` and the second for `POSTGRES_PASSWORD`. URL-safe database passwords avoid connection-string parsing problems.

Accounts are created through the app’s registration screen. Passwords must contain at least 12 characters. Access tokens expire after 12 hours by default; configure `ACCESS_TOKEN_EXPIRE_MINUTES` to change this. Set `CORS_ORIGINS` to a comma-separated list of trusted frontend origins when deploying.

Registration sends a verification email when SMTP is configured and delivery succeeds. If SMTP is not configured or email delivery fails, a new account is automatically verified so the user can sign in. Set `AUTO_VERIFY_EMAIL=true` to always verify accounts immediately; the app still attempts to send verification emails whenever SMTP is configured. Automatic verification does not prove email ownership, so use it only where that tradeoff is acceptable.

Registration is limited to 5 attempts per client IP per hour, and login to 10 attempts per client IP per minute. Configure `AUTH_REGISTER_LIMIT`, `AUTH_REGISTER_WINDOW_SECONDS`, `AUTH_LOGIN_LIMIT`, and `AUTH_LOGIN_WINDOW_SECONDS` to adjust these limits. Counters are stored in the shared metadata database. If running behind a reverse proxy, configure the server to trust forwarded client IPs only from that proxy so limits apply to the real client address.

Visitors can use **Continue as guest** without creating an account. Each guest receives a private workspace that expires after 24 hours; expired files and vectors are cleaned up periodically. The bearer token is kept in browser session storage, so closing the tab loses access while the data remains until expiration cleanup. Guest limits default to 3 PDFs, 5 MB, 100 pages, and 1 million extracted characters per PDF, 5 uploads per hour, 20 questions per hour, and 10 new guest sessions per client IP per hour. Configure the `GUEST_*` variables in `.env` to adjust these limits. Guests can explicitly end a session to delete its documents immediately. Document text is sent to Gemini for embeddings and answers. Guest workspaces are temporary, not recoverable, and must not be used for sensitive documents.

Docker Compose does not publish the backend port; its default `FORWARDED_ALLOW_IPS=*` is intended only for that private Compose network. If you publish the backend or attach untrusted containers to the network, set `FORWARDED_ALLOW_IPS` to the exact trusted proxy address(es). A public reverse proxy must overwrite forwarded client-IP headers rather than trust values supplied by clients.

Email verification and password recovery use standard SMTP. For Gmail, enable 2-Step Verification and create a Google App Password; use that App Password, not your Google account password. Some Google Workspace accounts or administrator policies may not allow App Passwords. Configure `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME` and `SMTP_FROM_EMAIL` as your Gmail address, `SMTP_PASSWORD` as the App Password, `SMTP_STARTTLS=true`, and `SMTP_USE_SSL=false`. Put these settings in `backend/.env` for local backend development or the root `.env` for Docker Compose. Do not share or commit the App Password. For other SMTP providers, use their documented host and authentication settings; port 465 normally uses `SMTP_USE_SSL=true` and `SMTP_STARTTLS=false`. Set `FRONTEND_URL` to the address users open so emailed links return to the app. Verification and password-reset links expire after 30 minutes by default.

## Run locally

In one terminal:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
notepad .env
# Add GOOGLE_API_KEY, AUTH_SECRET_KEY (32+ characters), and AUTO_VERIFY_EMAIL=true.
# SQLite is used by default; SMTP is optional.
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open the frontend at <http://localhost:5173> and the API documentation at <http://localhost:8000/docs>.

## Run with Docker Compose

Create the root `.env` as described above, then run:

```sh
docker compose up --build
```

Docker Compose starts PostgreSQL for document metadata and persists PostgreSQL, ChromaDB, and uploaded files in named volumes. The app is served at <http://localhost:5173>; the API and interactive documentation are available through <http://localhost:5173/docs>. The backend and database are not published directly to the host. The Compose frontend binds to loopback by default; place it behind an HTTPS reverse proxy for public access and set `FRONTEND_URL` and `CORS_ORIGINS` to the public HTTPS origin.

## Deploy from GitHub on Railway

Railway can deploy the frontend and backend directly from this repository and provides HTTPS domains without a separately purchased domain. Use the Hobby plan for persistent storage; it starts at $5/month and includes usage-based compute and storage charges, so configure a spend limit. Check [current Railway pricing](https://docs.railway.com/pricing). Railway Free is limited to one 0.5 GB volume per project, which is not a good fit for a backend data volume plus a database.

1. Create an empty Railway project. Add empty services named exactly `backend` and `frontend`, then add Railway's PostgreSQL service. In each app service's settings, set its root directory (`/backend` or `/frontend`) and connect the GitHub repository `kvkrpm/DocuMind-AI`. Use the Dockerfile in that service's root directory. Railway documents this [isolated monorepo workflow](https://docs.railway.com/deployments/monorepo).
2. Attach a Railway volume to the backend service, mounted at `/app/data`. The backend image creates the data directories and runs the API as its non-root `app` user.
3. Configure the **backend** service variables in Railway (use the dashboard's variable-reference picker for the PostgreSQL `DATABASE_URL`):

   ```text
   DATABASE_URL=<reference to PostgreSQL DATABASE_URL>
   GOOGLE_API_KEY=<your Gemini API key>
   AUTH_SECRET_KEY=<generated random secret, at least 32 characters>
   CHROMA_PERSIST_DIR=/app/data/chroma_db
   UPLOAD_DIR=/app/data/uploads
   FORWARDED_ALLOW_IPS=*
   ```

   `FORWARDED_ALLOW_IPS=*` is only appropriate while the backend remains private to this Railway project and receives requests through the frontend proxy. Do not expose the backend directly with this setting.
4. Configure the **frontend** service variable:

   ```text
   BACKEND_URL=http://backend.railway.internal:8000
   ```

   Keep the service name `backend` so Railway's private DNS name matches. The frontend proxies API requests over Railway's private network; only the frontend needs a public domain.
5. Deploy the frontend, then use its Railway **Generate Domain** action (target port `80` if prompted). Set the backend variables `FRONTEND_URL` and `CORS_ORIGINS` to that generated HTTPS URL, then redeploy the backend. A purchased domain is optional.
6. Smoke-test guest session creation, upload a small text-based PDF, ask a question and check its page citation, end the guest session, and confirm a redeploy preserves the PostgreSQL and backend volume data.

Guest mode does not require SMTP. If you want account registration and password recovery to work, configure the SMTP variables too; without them, email-based registration cannot complete. Add your Gemini key and secrets only in Railway's Variables dashboard, never in GitHub. Set Railway spending controls and monitor Gemini API quota; Railway hosting and Gemini usage are separate costs.

## Test

GitHub Actions runs the backend tests on Python 3.10 and 3.11, the frontend production build, and both production Docker image builds on pushes and pull requests. The Python requirements are pinned to the versions tested by the project. To run the backend unit and API tests locally:

```powershell
cd backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Install the exact frontend dependencies from the lockfile and build:

```powershell
cd frontend
npm ci
npm run build
```

The tests do not call Gemini or require an API key. Uploading and querying documents in the running app does require a valid key and an internet connection.

## How it works

- **PDF extraction:** `pypdf` extracts page text and preserves page numbers. Scanned/image-only PDFs need OCR and are not supported.
- **Chunking:** LangChain's recursive text splitter creates overlapping chunks and attaches document and page metadata.
- **Embeddings and retrieval:** `models/gemini-embedding-001` vectors are stored in ChromaDB. Each query searches only the current account or guest workspace: ChromaDB returns the top 10 dense vector matches, while `BM25Okapi` searches that workspace's indexed chunk text for the top 10 lexical matches. Reciprocal Rank Fusion (RRF, `k=60`) merges them into up to 12 unique candidates, which a lazy-loaded FlashRank `ms-marco-TinyBERT-L-2-v2` cross-encoder reranks to select the top 4. The reranker uses ONNX Runtime and adds no PyTorch dependency; its measured process footprint was about 71 MiB in the local test environment. If reranking fails, the service falls back to the RRF ranking.
- **Answers:** Gemini receives the retrieved passages and a grounded prompt; the API returns the answer, source citations, and a sufficient-context flag.
- **Persistence:** PostgreSQL stores document metadata in Docker Compose; local backend development defaults to SQLite. ChromaDB vectors and uploaded files are stored separately. With Docker Compose they use named volumes.
- **Accounts and privacy:** Each account has a separate document list and can query or delete only its own documents. Passwords are stored as salted scrypt hashes; API access uses expiring bearer tokens.
- **Guest privacy:** Guest workspaces use separate bearer tokens and ownership IDs. They expire after the configured lifetime and are removed by background cleanup; guests can also delete their workspace immediately.
- **Deployment:** The Compose stack uses non-reload backend runtime, non-root backend container, same-origin API proxying, and database-backed health checks. TLS must be terminated by a trusted HTTPS reverse proxy before exposing the app publicly.

### Retrieval Evaluation Benchmark

The offline benchmark compares the three retrieval pipelines on 12 semantic and exact-keyword test questions:

| Pipeline | Recall@4 (Hit Rate) | MRR@4 | Avg latency (ms/query) |
|---|---:|---:|---:|
| Dense Vector Only | 0.917 | 0.292 | 0.024 |
| Hybrid (BM25 + Vector RRF) | 0.917 | 0.708 | 3.083 |
| Hybrid + TinyBERT Reranker | 1.000 | 1.000 | 19.678 |

Recall@4 is reported as hit rate: the fraction of questions with at least one relevant chunk in the first four results. MRR@4 is the mean reciprocal rank of the first relevant chunk, with misses scored as zero.

This is an API-free benchmark. Dense results are fixed rankings in `sample_dataset.json`, not live Gemini/Chroma measurements; latency covers local ranking only and excludes embedding and answer-generation calls. FlashRank is loaded once and kept out of per-query latency via warm-up. For air-gapped runs, pre-populate the configured FlashRank cache; an uncached model is downloaded on first use.

## API

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Backend health and configured model |
| `POST` | `/api/auth/register` | Create an account and send an email-verification link when SMTP succeeds |
| `POST` | `/api/auth/login` | Sign in and receive an access token |
| `GET` | `/api/auth/me` | Return the signed-in account |
| `POST` | `/api/auth/verify-email` | Verify an email using the one-time token |
| `POST` | `/api/auth/verify-email/resend` | Resend an email-verification link |
| `POST` | `/api/auth/password/forgot` | Request a password-reset link |
| `POST` | `/api/auth/password/reset` | Set a new password with the one-time token |
| `POST` | `/api/guest/session` | Start a temporary private guest workspace (rate limited) |
| `DELETE` | `/api/guest/session` | Delete the current guest workspace and its documents |
| `GET` | `/api/documents` | List the signed-in account’s documents (requires bearer token) |
| `POST` | `/api/documents/upload` | Upload a PDF as multipart field `file` (requires bearer token) |
| `DELETE` | `/api/documents/{doc_id}` | Delete an owned document and its indexed chunks (requires bearer token) |
| `POST` | `/api/query` | Ask about owned documents; accepts `{"question": "...", "doc_ids": ["..."]}` (requires bearer token) |

Account uploads are limited to 10 MB and 30 documents by default. Guest limits are lower and configurable with the `GUEST_*` environment variables. Configure `MAX_FILE_SIZE_MB`, `MAX_DOCS`, `GEMINI_MODEL`, and `GEMINI_EMBEDDING_MODEL` as well.

New accounts must verify their email before signing in. Password-reset links are single-use; changing a password invalidates the account’s existing access tokens. Password-recovery responses do not disclose whether an email is registered. Existing documents from before accounts were added remain unassigned and are not visible to new accounts. Re-upload them under the account that should own them. Use HTTPS in deployment.

## Troubleshooting

- **Embedding quota exceeded (HTTP 429 / `RESOURCE_EXHAUSTED`):** Gemini rate-limits embedding requests. Wait at least 60 seconds before retrying. If the limit persists, check the Google AI Studio project's [Gemini API rate limits and quota](https://ai.google.dev/gemini-api/docs/rate-limits) and billing. Large PDFs produce more text chunks and therefore use more embedding requests.
- **No extractable text:** The PDF may be scanned or image-only. This app extracts selectable text and does not currently perform OCR.
- **API key or model errors:** Verify `GOOGLE_API_KEY`, `GEMINI_MODEL`, and `GEMINI_EMBEDDING_MODEL` in the environment file used by your run mode.
