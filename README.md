# Document QA RAG Platform

Upload text-based PDFs, ask questions about their contents, and get answers with document and page citations.

## Requirements

- Python 3.10 or later
- Node.js 20.19+ or 22.12+ (for local frontend development)
- A Google AI Studio API key for Gemini embeddings and answers
- Docker Compose (optional)

## Configure

Create a `.env` file in the project root using `.env.example` as a template. Set `GOOGLE_API_KEY`, replace `POSTGRES_PASSWORD` with a strong URL-safe password, set `AUTH_SECRET_KEY` to a random value of at least 32 characters, and configure SMTP before running Docker Compose. Do not commit `.env`.

For local backend development, copy the required settings into `backend/.env`. Set `AUTH_SECRET_KEY` there as well; the backend uses SQLite by default, and `DATABASE_URL` can select another database. If no auth key is set in local development, the app generates a temporary key and existing sessions will stop working after a restart. The root `.env` is used by Docker Compose.

Generate secrets using Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the first generated value for `AUTH_SECRET_KEY` and the second for `POSTGRES_PASSWORD`. URL-safe database passwords avoid connection-string parsing problems.

Accounts are created through the app’s registration screen. Passwords must contain at least 12 characters. Access tokens expire after 12 hours by default; configure `ACCESS_TOKEN_EXPIRE_MINUTES` to change this. Set `CORS_ORIGINS` to a comma-separated list of trusted frontend origins when deploying.

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

## Test

GitHub Actions runs the backend tests and frontend production build on pushes and pull requests. The Python requirements are pinned to the versions tested by the project. To run the backend unit and API tests locally:

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
- **Embeddings and retrieval:** Gemini embeddings are stored in a persistent ChromaDB collection. Queries retrieve the five nearest chunks by cosine similarity.
- **Answers:** Gemini receives the retrieved passages and a grounded prompt; the API returns the answer, source citations, and a sufficient-context flag.
- **Persistence:** PostgreSQL stores document metadata in Docker Compose; local backend development defaults to SQLite. ChromaDB vectors and uploaded files are stored separately. With Docker Compose they use named volumes.
- **Accounts and privacy:** Each account has a separate document list and can query or delete only its own documents. Passwords are stored as salted scrypt hashes; API access uses expiring bearer tokens.
- **Guest privacy:** Guest workspaces use separate bearer tokens and ownership IDs. They expire after the configured lifetime and are removed by background cleanup; guests can also delete their workspace immediately.
- **Deployment:** The Compose stack uses non-reload backend runtime, non-root backend container, same-origin API proxying, and database-backed health checks. TLS must be terminated by a trusted HTTPS reverse proxy before exposing the app publicly.

## API

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Backend health and configured model |
| `POST` | `/api/auth/register` | Create an account and send an email-verification link |
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
