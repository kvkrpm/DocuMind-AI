import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pypdf.errors import PdfReadError
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.services.pdf_service import validate_file, extract_pdf_with_pages, chunk_pages
from app.services.rag_service import rag_service
from app.services.document_store import document_store
from app.services import email_service
from app.services.auth_service import (
    create_action_token,
    decode_access_token,
    decode_guest_token,
    hash_action_token,
    hash_password,
    issue_guest_token,
    issue_access_token,
    verify_login_password,
)
from app.models.schemas import (
    AuthRequest,
    EmailRequest,
    PasswordResetRequest,
    QueryRequest,
    TokenRequest,
)

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


def delete_guest_documents(guest_id: str) -> None:
    for document in document_store.list_documents(guest_id):
        rag_service.delete_doc(document["id"])
        file_path = os.path.join(settings.UPLOAD_DIR, f"{document['id']}.pdf")
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        document_store.delete_document(document["id"], guest_id)


def cleanup_expired_guest_sessions() -> None:
    for guest_id in document_store.list_expired_guest_sessions():
        try:
            delete_guest_documents(guest_id)
            document_store.delete_guest_session(guest_id)
        except Exception:
            logger.exception("Could not clean up expired guest workspace %s", guest_id)


async def guest_cleanup_loop() -> None:
    while True:
        await asyncio.sleep(900)
        try:
            await asyncio.to_thread(cleanup_expired_guest_sessions)
        except Exception:
            logger.exception("Could not run expired guest workspace cleanup")


@asynccontextmanager
async def lifespan(_app):
    await asyncio.to_thread(cleanup_expired_guest_sessions)
    cleanup_task = asyncio.create_task(guest_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task


app = FastAPI(
    title="Document QA RAG Platform (Gemini)",
    version="1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            401,
            "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    decoded = decode_access_token(credentials.credentials)
    user = document_store.get_user(decoded[0]) if decoded else None
    if (
        user is None
        or not user["email_verified"]
        or user["token_version"] != decoded[1]
    ):
        raise HTTPException(
            401,
            "Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_workspace(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            401,
            "Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access = decode_access_token(credentials.credentials)
    if access is not None:
        user = document_store.get_user(access[0])
        if (
            user is not None
            and user["email_verified"]
            and user["token_version"] == access[1]
        ):
            return {"id": user["id"], "is_guest": False}
    guest_id = decode_guest_token(credentials.credentials)
    if guest_id is not None and document_store.get_active_guest_session(guest_id):
        return {"id": guest_id, "is_guest": True}
    raise HTTPException(
        401,
        "Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_guest(
    workspace: dict = Depends(get_current_workspace),
):
    if not workspace["is_guest"]:
        raise HTTPException(403, "This action is only available to guest sessions")
    return workspace


def create_auth_response(user: dict):
    return {
        "access_token": issue_access_token(
            user["id"],
            user.get("token_version", 0),
        ),
        "token_type": "bearer",
        "user": {"id": user["id"], "email": user["email"]},
    }


def enforce_rate_limit(
    request: Request,
    scope: str,
    limit: int,
    window_seconds: int,
    client_key: str | None = None,
) -> None:
    if client_key is None:
        client_key = request.client.host if request.client else "unknown"
    hits, retry_after = document_store.consume_auth_attempt(
        scope,
        client_key,
        window_seconds,
    )
    if hits > limit:
        raise HTTPException(
            429,
            "Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def create_email_action(user: dict, purpose: str) -> str:
    if purpose == "verify_email":
        expiry_minutes = settings.EMAIL_TOKEN_EXPIRE_MINUTES
        query_key = "verify_token"
        subject = "Verify your Document QA account"
        action = "verify your email address"
    else:
        expiry_minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
        query_key = "reset_token"
        subject = "Reset your Document QA password"
        action = "reset your password"

    token, token_hash, expires_at = create_action_token(expiry_minutes)
    document_store.create_auth_token(
        token_hash,
        user["id"],
        purpose,
        expires_at,
    )
    link = f"{settings.FRONTEND_URL}/#{query_key}={token}"
    try:
        email_service.send_email(
            user["email"],
            subject,
            (
                f"Use the following link to {action}:\n\n{link}\n\n"
                f"This link expires in {expiry_minutes} minutes and can only be used once. "
                "If you did not request this, you can ignore this message."
            ),
        )
    except Exception as e:
        raise EmailDeliveryError("Could not deliver email") from e
    return token


@app.get("/health")
def health():
    document_store.check_connection()
    return {
        "status": "ok",
        "provider": settings.LLM_PROVIDER,
        "model": settings.GEMINI_MODEL,
    }

@app.post("/api/auth/register", status_code=201)
def register(req: AuthRequest, request: Request):
    enforce_rate_limit(
        request,
        "register",
        settings.AUTH_REGISTER_LIMIT,
        settings.AUTH_REGISTER_WINDOW_SECONDS,
    )
    try:
        user = document_store.create_user(
            req.email,
            hash_password(req.password),
        )
    except IntegrityError as e:
        raise HTTPException(409, "An account with this email already exists") from e
    email_delivery_failed = not email_service.is_configured()
    email_sent = False
    if settings.AUTO_VERIFY_EMAIL:
        user = document_store.mark_email_verified(user["id"])
        if user is None:
            raise RuntimeError("Newly created user could not be verified")

    if email_delivery_failed:
        logger.warning(
            "SMTP is not configured; automatically verifying new user %s",
            user["id"],
        )
    else:
        try:
            create_email_action(user, "verify_email")
        except EmailDeliveryError:
            email_delivery_failed = True
            logger.exception(
                "Could not send account verification email; automatically verifying user %s",
                user["id"],
            )
        else:
            email_sent = True

    if email_delivery_failed and not user["email_verified"]:
        user = document_store.mark_email_verified(user["id"])
        if user is None:
            raise RuntimeError("Newly created user could not be verified")

    if email_delivery_failed:
        message = (
            "Account created and verified because email could not be delivered. "
            "You can sign in now."
        )
    elif settings.AUTO_VERIFY_EMAIL:
        message = "Account created and verified. You can sign in now."
        if email_sent:
            message += " A verification email was also sent."
    else:
        message = "Account created. Check your email for the verification link."
    return {
        "message": message,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "email_verified": user["email_verified"],
        },
    }


@app.post("/api/auth/verify-email/resend")
def resend_verification(req: EmailRequest, request: Request):
    enforce_rate_limit(
        request,
        "verify_email",
        settings.AUTH_RECOVERY_LIMIT,
        settings.AUTH_RECOVERY_WINDOW_SECONDS,
    )
    if not email_service.is_configured():
        raise HTTPException(
            503,
            "Email delivery is not configured. Set SMTP_HOST and SMTP_FROM_EMAIL.",
        )
    user = document_store.get_user_by_email(req.email)
    if user is not None and not user["email_verified"]:
        try:
            create_email_action(user, "verify_email")
        except Exception as e:
            logger.exception("Could not send account verification email")
            raise HTTPException(
                503,
                "Could not send verification email. Please try again later.",
            ) from e
    return {"message": "If the account needs verification, an email will be sent."}


@app.post("/api/auth/verify-email")
def verify_email(req: TokenRequest):
    user_id = document_store.consume_auth_token(
        hash_action_token(req.token),
        "verify_email",
    )
    if user_id is None:
        raise HTTPException(400, "Verification link is invalid or expired")
    user = document_store.mark_email_verified(user_id)
    if user is None:
        raise HTTPException(400, "Verification link is invalid or expired")
    return create_auth_response(user)


@app.post("/api/auth/login")
def login(req: AuthRequest, request: Request):
    enforce_rate_limit(
        request,
        "login",
        settings.AUTH_LOGIN_LIMIT,
        settings.AUTH_LOGIN_WINDOW_SECONDS,
    )
    user = document_store.get_user_by_email(req.email)
    if not verify_login_password(
        req.password,
        user["password_hash"] if user else None,
    ):
        raise HTTPException(401, "Invalid email or password")
    if not user["email_verified"]:
        raise HTTPException(
            403,
            "Email verification required. Check your inbox or request a new link.",
        )
    return create_auth_response(user)


@app.post("/api/auth/password/forgot")
def forgot_password(req: EmailRequest, request: Request):
    enforce_rate_limit(
        request,
        "password_recovery",
        settings.AUTH_RECOVERY_LIMIT,
        settings.AUTH_RECOVERY_WINDOW_SECONDS,
    )
    user = document_store.get_user_by_email(req.email)
    if user is not None and user["email_verified"] and email_service.is_configured():
        try:
            create_email_action(user, "reset_password")
        except Exception:
            logger.exception("Could not send password recovery email")
    return {
        "message": "If an account exists for that email, a password reset link will be sent."
    }


@app.post("/api/auth/password/reset")
def reset_password(req: PasswordResetRequest):
    user_id = document_store.consume_auth_token(
        hash_action_token(req.token),
        "reset_password",
    )
    if user_id is None:
        raise HTTPException(400, "Password reset link is invalid or expired")
    if not document_store.reset_user_password(user_id, hash_password(req.password)):
        raise HTTPException(400, "Password reset link is invalid or expired")
    return {"message": "Password reset successfully. Please sign in again."}


@app.get("/api/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
    }


@app.post("/api/guest/session", status_code=201)
def create_guest_session(request: Request):
    enforce_rate_limit(
        request,
        "guest_session",
        settings.GUEST_SESSION_LIMIT,
        settings.GUEST_SESSION_WINDOW_SECONDS,
    )
    guest_id, expires_at = document_store.create_guest_session(
        settings.GUEST_SESSION_TTL_HOURS
    )
    return {
        "access_token": issue_guest_token(guest_id, expires_at),
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "limits": {
            "max_docs": settings.GUEST_MAX_DOCS,
            "max_file_size_mb": settings.GUEST_MAX_FILE_SIZE_MB,
            "max_pages": settings.GUEST_MAX_PAGES,
        },
    }


@app.delete("/api/guest/session")
def delete_guest_session(guest: dict = Depends(get_current_guest)):
    guest_id = guest["id"]
    delete_guest_documents(guest_id)
    if not document_store.delete_guest_session(guest_id):
        raise HTTPException(404, "Guest session not found")
    return {"deleted": True}


@app.get("/api/documents")
def list_docs(workspace: dict = Depends(get_current_workspace)):
    return document_store.list_documents(workspace["id"])

@app.post("/api/documents/upload")
async def upload_doc(
    request: Request,
    file: UploadFile = File(...),
    workspace: dict = Depends(get_current_workspace),
):
    if workspace["is_guest"]:
        enforce_rate_limit(
            request,
            "guest_upload",
            settings.GUEST_UPLOAD_LIMIT,
            settings.GUEST_UPLOAD_WINDOW_SECONDS,
            workspace["id"],
        )
        max_docs = settings.GUEST_MAX_DOCS
        max_file_size_mb = settings.GUEST_MAX_FILE_SIZE_MB
    else:
        max_docs = settings.MAX_DOCS
        max_file_size_mb = settings.MAX_FILE_SIZE_MB
    if document_store.count_documents(workspace["id"]) >= max_docs:
        raise HTTPException(400, f"Workspace limit reached: {max_docs} docs max")

    try:
        validate_file(0, file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

    guest_slot_reserved = False
    if workspace["is_guest"]:
        guest_slot_reserved = document_store.reserve_guest_document(
            workspace["id"],
            max_docs,
        )
        if not guest_slot_reserved:
            raise HTTPException(400, f"Workspace limit reached: {max_docs} docs max")

    def release_guest_slot() -> None:
        if guest_slot_reserved:
            try:
                document_store.release_guest_document(workspace["id"])
            except Exception:
                logger.exception("Could not release guest document reservation")

    doc_id = str(uuid.uuid4())
    file_path = os.path.join(settings.UPLOAD_DIR, f"{doc_id}.pdf")
    max_bytes = max_file_size_mb * 1024 * 1024
    file_size = 0
    try:
        with open(file_path, "wb") as stored_file:
            while chunk := await file.read(64 * 1024):
                file_size += len(chunk)
                if file_size > max_bytes:
                    raise ValueError(
                        f"File exceeds {max_file_size_mb}MB limit"
                    )
                stored_file.write(chunk)
    except ValueError as e:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(400, str(e)) from e
    except Exception:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    try:
        pages_data, total_pages = extract_pdf_with_pages(
            file_path,
            max_pages=(
                settings.GUEST_MAX_PAGES if workspace["is_guest"] else None
            ),
            max_chars=(
                settings.GUEST_MAX_EXTRACTED_CHARS
                if workspace["is_guest"]
                else None
            ),
        )
        if not pages_data:
            raise HTTPException(
                400,
                "No selectable text found. Scanned or image-only PDFs are not supported.",
            )
        if workspace["is_guest"] and total_pages > settings.GUEST_MAX_PAGES:
            raise HTTPException(
                400,
                f"Guest PDFs are limited to {settings.GUEST_MAX_PAGES} pages",
            )
        chunks = chunk_pages(pages_data, doc_id, file.filename)
    except HTTPException:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    except (PdfReadError, ValueError) as e:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(400, f"Could not read PDF: {e}") from e
    except Exception as e:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(500, f"Processing failed: {str(e)}") from e

    try:
        chunk_count = rag_service.add_chunks(chunks)
    except Exception as e:
        release_guest_slot()
        if os.path.exists(file_path):
            os.remove(file_path)
        if getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e).upper():
            raise HTTPException(
                429,
                "Gemini's embedding quota has been reached. Wait at least 60 seconds "
                "before retrying, reduce the document size, or check your API quota "
                "and billing at https://ai.google.dev/gemini-api/docs/rate-limits."
            ) from e
        raise HTTPException(500, f"Processing failed: {str(e)}")

    info = {
        "id": doc_id,
        "name": file.filename,
        "size_mb": round(file_size/1024/1024, 2),
        "pages": total_pages,
        "chunks": chunk_count,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    try:
        document_store.add_document(info, workspace["id"])
    except Exception as e:
        cleanup_errors = []
        try:
            rag_service.delete_doc(doc_id)
        except Exception as cleanup_error:
            cleanup_errors.append(str(cleanup_error))
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as cleanup_error:
            cleanup_errors.append(str(cleanup_error))
        detail = f"Metadata persistence failed: {e}"
        if cleanup_errors:
            detail += f"; cleanup failed: {'; '.join(cleanup_errors)}"
        release_guest_slot()
        raise HTTPException(500, detail) from e
    return info

@app.delete("/api/documents/{doc_id}")
def delete_doc(doc_id: str, workspace: dict = Depends(get_current_workspace)):
    if document_store.get_document(doc_id, workspace["id"]) is None:
        raise HTTPException(404, "Document not found")
    rag_service.delete_doc(doc_id)
    fp = os.path.join(settings.UPLOAD_DIR, f"{doc_id}.pdf")
    if os.path.exists(fp):
        os.remove(fp)
    document_store.delete_document(doc_id, workspace["id"])
    return {"deleted": doc_id}

@app.post("/api/query")
def query_docs(
    req: QueryRequest,
    request: Request,
    workspace: dict = Depends(get_current_workspace),
):
    if not req.question.strip():
        raise HTTPException(400, "Question cannot be empty")
    if workspace["is_guest"]:
        enforce_rate_limit(
            request,
            "guest_query",
            settings.GUEST_QUERY_LIMIT,
            settings.GUEST_QUERY_WINDOW_SECONDS,
            workspace["id"],
        )
    documents = document_store.list_documents(workspace["id"])
    owned_ids = {document["id"] for document in documents}
    requested_ids = req.doc_ids if req.doc_ids is not None else list(owned_ids)
    if any(doc_id not in owned_ids for doc_id in requested_ids):
        raise HTTPException(404, "Document not found")
    if not requested_ids:
        return {
            "answer": "The documents do not contain enough information to answer this question.",
            "sources": [],
            "has_sufficient_context": False,
        }
    try:
        result = rag_service.query(
            req.question,
            doc_ids=requested_ids,
            k=settings.TOP_K,
        )
        return result
    except Exception as e:
        raise HTTPException(500, f"Query failed: {str(e)}. Check GOOGLE_API_KEY in .env")
