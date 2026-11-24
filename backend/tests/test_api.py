import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from app import main
from app.core.config import settings
from app.services.auth_service import (
    create_action_token,
    hash_action_token,
    hash_password,
    issue_access_token,
)
from app.services.document_store import DocumentStore, GuestSession
from app.services.pdf_service import extract_pdf_with_pages


def make_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"\nendstream",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(pdf)


class DocumentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.upload_dir = Path(self.temp_dir.name)
        self.upload_dir_patch = patch.object(settings, "UPLOAD_DIR", str(self.upload_dir))
        self.upload_dir_patch.start()
        self.addCleanup(self.upload_dir_patch.stop)
        self.document_store = DocumentStore("sqlite:///:memory:")
        self.addCleanup(self.document_store.close)
        self.store_patch = patch.object(main, "document_store", self.document_store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.secret_patch = patch.object(
            settings,
            "AUTH_SECRET_KEY",
            "test-secret-key-for-auth-tests-that-is-long-enough",
        )
        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)
        self.user = self.document_store.create_user(
            "owner@example.com",
            hash_password("test-password-123"),
            email_verified=True,
        )
        self.client = TestClient(
            main.app,
            headers={
                "Authorization": f"Bearer {issue_access_token(self.user['id'])}"
            },
        )

    def add_document(self, document_id: str, owner_id: str) -> None:
        self.document_store.add_document(
            {
                "id": document_id,
                "name": f"{document_id}.pdf",
                "size_mb": 0.1,
                "pages": 1,
                "chunks": 1,
                "created_at": "2025-01-01T00:00:00+00:00",
            },
            owner_id,
        )

    def create_guest_client(self) -> TestClient:
        response = self.client.post("/api/guest/session")
        self.assertEqual(response.status_code, 201)
        return TestClient(
            main.app,
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        )

    def test_auth_registers_logs_in_and_rejects_duplicate_accounts(self):
        with (
            patch.object(settings, "AUTO_VERIFY_EMAIL", False),
            patch.object(main.email_service, "is_configured", return_value=True),
            patch.object(main.email_service, "send_email") as send_email,
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": " New.User@example.com ",
                    "password": "a-long-test-password",
                },
            )

        self.assertEqual(response.status_code, 201)
        auth_data = response.json()
        self.assertEqual(auth_data["user"]["email"], "new.user@example.com")
        self.assertFalse(auth_data["user"]["email_verified"])
        self.assertNotIn("access_token", auth_data)
        verification_token = re.search(
            r"verify_token=([A-Za-z0-9_-]+)",
            send_email.call_args.args[2],
        ).group(1)
        unverified_login = self.client.post(
            "/api/auth/login",
            json={
                "email": "new.user@example.com",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(unverified_login.status_code, 403)
        verified = self.client.post(
            "/api/auth/verify-email",
            json={"token": verification_token},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["user"]["email"], "new.user@example.com")
        authenticated = TestClient(
            main.app,
            headers={"Authorization": f"Bearer {verified.json()['access_token']}"},
        )
        self.assertEqual(
            authenticated.get("/api/auth/me").json(),
            verified.json()["user"],
        )
        reused_link = self.client.post(
            "/api/auth/verify-email",
            json={"token": verification_token},
        )

        with patch.object(main.email_service, "is_configured", return_value=True):
            duplicate = self.client.post(
                "/api/auth/register",
                json={
                    "email": "new.user@example.com",
                    "password": "a-long-test-password",
                },
            )
        login = self.client.post(
            "/api/auth/login",
            json={"email": "NEW.USER@example.com", "password": "a-long-test-password"},
        )
        invalid_login = self.client.post(
            "/api/auth/login",
            json={"email": "new.user@example.com", "password": "wrong-password-123"},
        )

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(reused_link.status_code, 400)
        self.assertEqual(login.status_code, 200)
        self.assertEqual(invalid_login.status_code, 401)

    def test_registration_auto_verifies_when_smtp_is_not_configured(self):
        with (
            patch.object(settings, "AUTO_VERIFY_EMAIL", False),
            patch.object(main.email_service, "is_configured", return_value=False),
            patch.object(main.email_service, "send_email") as send_email,
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": "no.smtp@example.com",
                    "password": "a-long-test-password",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertIn("verified", response.json()["message"].lower())
        self.assertTrue(response.json()["user"]["email_verified"])
        self.assertTrue(
            self.document_store.get_user_by_email("no.smtp@example.com")[
                "email_verified"
            ]
        )
        send_email.assert_not_called()
        login = self.client.post(
            "/api/auth/login",
            json={
                "email": "no.smtp@example.com",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(login.status_code, 200)

    def test_registration_auto_verifies_when_smtp_send_fails(self):
        with (
            patch.object(settings, "AUTO_VERIFY_EMAIL", False),
            patch.object(main.email_service, "is_configured", return_value=True),
            patch.object(
                main.email_service,
                "send_email",
                side_effect=OSError("SMTP connection refused"),
            ),
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": "smtp.failed@example.com",
                    "password": "a-long-test-password",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["user"]["email_verified"])
        self.assertTrue(
            self.document_store.get_user_by_email("smtp.failed@example.com")[
                "email_verified"
            ]
        )
        login = self.client.post(
            "/api/auth/login",
            json={
                "email": "smtp.failed@example.com",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(login.status_code, 200)

    def test_auto_verify_setting_still_sends_configured_verification_email(self):
        with (
            patch.object(settings, "AUTO_VERIFY_EMAIL", True),
            patch.object(main.email_service, "is_configured", return_value=True),
            patch.object(main.email_service, "send_email") as send_email,
        ):
            response = self.client.post(
                "/api/auth/register",
                json={
                    "email": "auto.verified@example.com",
                    "password": "a-long-test-password",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertIn("verified", response.json()["message"].lower())
        self.assertTrue(response.json()["user"]["email_verified"])
        send_email.assert_called_once()
        self.assertTrue(
            self.document_store.get_user_by_email("auto.verified@example.com")[
                "email_verified"
            ]
        )
        login = self.client.post(
            "/api/auth/login",
            json={
                "email": "auto.verified@example.com",
                "password": "a-long-test-password",
            },
        )
        self.assertEqual(login.status_code, 200)

    def test_registration_rate_limit_returns_retry_after(self):
        with (
            patch.object(settings, "AUTH_REGISTER_LIMIT", 1),
            patch.object(main.email_service, "is_configured", return_value=True),
            patch.object(main.email_service, "send_email"),
        ):
            first = self.client.post(
                "/api/auth/register",
                json={
                    "email": "first@example.com",
                    "password": "first-test-password-123",
                },
            )
            blocked = self.client.post(
                "/api/auth/register",
                json={
                    "email": "second@example.com",
                    "password": "second-test-password-123",
                },
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(blocked.status_code, 429)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)

    def test_login_rate_limit_blocks_repeated_attempts(self):
        with patch.object(settings, "AUTH_LOGIN_LIMIT", 1):
            invalid = self.client.post(
                "/api/auth/login",
                json={
                    "email": "owner@example.com",
                    "password": "wrong-password-123",
                },
            )
            blocked = self.client.post(
                "/api/auth/login",
                json={
                    "email": "owner@example.com",
                    "password": "test-password-123",
                },
            )

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_password_reset_changes_password_invalidates_sessions_and_consumes_link(self):
        raw_token, token_hash, expires_at = create_action_token(30)
        self.document_store.create_auth_token(
            token_hash,
            self.user["id"],
            "reset_password",
            expires_at,
        )

        response = self.client.post(
            "/api/auth/password/reset",
            json={"token": raw_token, "password": "new-test-password-456"},
        )
        old_session = self.client.get("/api/auth/me")
        reused_link = self.client.post(
            "/api/auth/password/reset",
            json={"token": raw_token, "password": "another-new-password-789"},
        )
        login = self.client.post(
            "/api/auth/login",
            json={
                "email": "owner@example.com",
                "password": "new-test-password-456",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(old_session.status_code, 401)
        self.assertEqual(reused_link.status_code, 400)
        self.assertEqual(login.status_code, 200)

    def test_expired_password_reset_link_is_rejected(self):
        expired_token = "expired-reset-link-for-testing-only"
        self.document_store.create_auth_token(
            hash_action_token(expired_token),
            self.user["id"],
            "reset_password",
            datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        response = self.client.post(
            "/api/auth/password/reset",
            json={"token": expired_token, "password": "new-test-password-456"},
        )

        self.assertEqual(response.status_code, 400)

    def test_password_recovery_response_does_not_reveal_account_existence(self):
        with (
            patch.object(main.email_service, "is_configured", return_value=True),
            patch.object(main.email_service, "send_email") as send_email,
        ):
            existing = self.client.post(
                "/api/auth/password/forgot",
                json={"email": "owner@example.com"},
            )
            absent = self.client.post(
                "/api/auth/password/forgot",
                json={"email": "absent@example.com"},
            )

        self.assertEqual(existing.status_code, 200)
        self.assertEqual(existing.json(), absent.json())
        send_email.assert_called_once()

    def test_document_api_requires_authentication(self):
        client = TestClient(main.app)

        self.assertEqual(client.get("/api/documents").status_code, 401)

    def test_guest_can_upload_and_query_only_its_own_documents(self):
        guest_client = self.create_guest_client()
        with patch.object(main.rag_service, "add_chunks", return_value=1):
            upload = guest_client.post(
                "/api/documents/upload",
                files={"file": ("guest.pdf", make_pdf("Guest-only content."), "application/pdf")},
            )
        self.assertEqual(upload.status_code, 200)
        document = upload.json()
        self.assertEqual(guest_client.get("/api/documents").json(), [document])
        self.assertEqual(self.client.get("/api/documents").json(), [])

        result = {
            "answer": "Guest-only content.",
            "sources": [],
            "has_sufficient_context": True,
        }
        with patch.object(main.rag_service, "query", return_value=result) as query:
            response = guest_client.post(
                "/api/query",
                json={"question": "What does the PDF say?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        query.assert_called_once_with(
            "What does the PDF say?",
            doc_ids=[document["id"]],
            k=settings.TOP_K,
        )

    def test_guest_limits_upload_size_and_document_count(self):
        guest_client = self.create_guest_client()
        with (
            patch.object(settings, "GUEST_MAX_DOCS", 1),
            patch.object(settings, "GUEST_MAX_FILE_SIZE_MB", 1),
            patch.object(main.rag_service, "add_chunks", return_value=1),
        ):
            oversized = guest_client.post(
                "/api/documents/upload",
                files={
                    "file": (
                        "large.pdf",
                        b"x" * (1024 * 1024 + 1),
                        "application/pdf",
                    )
                },
            )
            accepted = guest_client.post(
                "/api/documents/upload",
                files={"file": ("small.pdf", make_pdf("Content."), "application/pdf")},
            )
            over_limit = guest_client.post(
                "/api/documents/upload",
                files={"file": ("another.pdf", make_pdf("More content."), "application/pdf")},
            )

        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(over_limit.status_code, 400)
        self.assertIn("Workspace limit", over_limit.json()["detail"])

    def test_guest_page_limit_rejects_pdf_before_embedding(self):
        guest_client = self.create_guest_client()
        with (
            patch.object(settings, "GUEST_MAX_PAGES", 100),
            patch.object(
                main,
                "extract_pdf_with_pages",
                return_value=([{"page_number": 1, "text": "content"}], 101),
            ),
            patch.object(main.rag_service, "add_chunks") as add_chunks,
        ):
            response = guest_client.post(
                "/api/documents/upload",
                files={"file": ("large.pdf", make_pdf("content"), "application/pdf")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("100 pages", response.json()["detail"])
        add_chunks.assert_not_called()
        self.assertEqual(list(self.upload_dir.glob("*.pdf")), [])

    def test_guest_text_limit_rejects_pdf_before_embedding(self):
        guest_client = self.create_guest_client()
        with (
            patch.object(
                main,
                "extract_pdf_with_pages",
                side_effect=ValueError("PDF contains too much extractable text"),
            ),
            patch.object(main.rag_service, "add_chunks") as add_chunks,
        ):
            response = guest_client.post(
                "/api/documents/upload",
                files={"file": ("large-text.pdf", make_pdf("content"), "application/pdf")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("too much extractable text", response.json()["detail"])
        add_chunks.assert_not_called()
        self.assertEqual(list(self.upload_dir.glob("*.pdf")), [])

    def test_pdf_extraction_stops_when_page_limit_is_exceeded(self):
        pages = [object(), object()]
        with patch("app.services.pdf_service.PdfReader") as pdf_reader:
            pdf_reader.return_value.pages = pages
            with self.assertRaisesRegex(ValueError, "1-page limit"):
                extract_pdf_with_pages("unused.pdf", max_pages=1)

    def test_pdf_extraction_rejects_excessive_text(self):
        page = type("Page", (), {"extract_text": lambda _self: "x" * 11})()
        with patch("app.services.pdf_service.PdfReader") as pdf_reader:
            pdf_reader.return_value.pages = [page]
            with self.assertRaisesRegex(ValueError, "too much extractable text"):
                extract_pdf_with_pages("unused.pdf", max_chars=10)

    def test_guest_query_rate_limit_returns_retry_after(self):
        guest_client = self.create_guest_client()
        guest_id = main.decode_guest_token(
            guest_client.headers["Authorization"].split(" ", 1)[1]
        )
        self.add_document("guest-document", guest_id)
        with (
            patch.object(settings, "GUEST_QUERY_LIMIT", 1),
            patch.object(main.rag_service, "query", return_value={"answer": "ok"}),
        ):
            first = guest_client.post("/api/query", json={"question": "first?"})
            blocked = guest_client.post("/api/query", json={"question": "second?"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_guest_can_end_session_and_remove_workspace_data(self):
        guest_client = self.create_guest_client()
        guest_id = main.decode_guest_token(
            guest_client.headers["Authorization"].split(" ", 1)[1]
        )
        self.add_document("guest-document", guest_id)
        stored_file = self.upload_dir / "guest-document.pdf"
        stored_file.write_bytes(b"temporary PDF")

        with patch.object(main.rag_service, "delete_doc") as delete_doc:
            response = guest_client.delete("/api/guest/session")

        self.assertEqual(response.status_code, 200)
        delete_doc.assert_called_once_with("guest-document")
        self.assertFalse(stored_file.exists())
        self.assertIsNone(self.document_store.get_document("guest-document"))
        self.assertFalse(self.document_store.get_active_guest_session(guest_id))
        self.assertEqual(guest_client.get("/api/documents").status_code, 401)

    def test_expired_guest_token_cannot_access_workspace(self):
        guest_client = self.create_guest_client()
        self.assertEqual(guest_client.get("/api/documents").json(), [])
        token = guest_client.headers["Authorization"].split(" ", 1)[1]
        guest_id = main.decode_guest_token(token)
        self.assertIsNotNone(guest_id)
        with self.document_store.session_factory.begin() as session:
            guest_session = session.get(GuestSession, guest_id)
            self.assertIsNotNone(guest_session)
            guest_session.expires_at = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            )

        expired = TestClient(
            main.app,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(expired.get("/api/documents").status_code, 401)

    def test_expired_guest_cleanup_removes_files_vectors_and_metadata(self):
        guest_id, _ = self.document_store.create_guest_session(1)
        self.add_document("expired-document", guest_id)
        stored_file = self.upload_dir / "expired-document.pdf"
        stored_file.write_bytes(b"temporary PDF")
        with self.document_store.session_factory.begin() as session:
            session.get(GuestSession, guest_id).expires_at = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            )

        with patch.object(main.rag_service, "delete_doc") as delete_doc:
            main.cleanup_expired_guest_sessions()

        delete_doc.assert_called_once_with("expired-document")
        self.assertFalse(stored_file.exists())
        self.assertIsNone(self.document_store.get_document("expired-document"))
        self.assertFalse(self.document_store.get_active_guest_session(guest_id))
        with self.document_store.session_factory() as session:
            self.assertIsNone(session.get(GuestSession, guest_id))

    def test_health_checks_metadata_database(self):
        with patch.object(self.document_store, "check_connection") as check_connection:
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        check_connection.assert_called_once_with()

    def test_upload_extracts_pages_and_indexes_chunks(self):
        pdf = make_pdf("The contract renews each year.")
        with patch.object(main.rag_service, "add_chunks", return_value=1) as add_chunks:
            response = self.client.post(
                "/api/documents/upload",
                files={"file": ("contract.pdf", pdf, "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        document = response.json()
        self.assertEqual(document["name"], "contract.pdf")
        self.assertEqual(document["pages"], 1)
        self.assertEqual(document["chunks"], 1)
        add_chunks.assert_called_once()
        chunks = add_chunks.call_args.args[0]
        self.assertIn("renews each year", chunks[0]["text"])
        self.assertEqual(chunks[0]["metadata"]["page_number"], 1)

        self.assertEqual(
            self.document_store.get_document(document["id"], self.user["id"])["name"],
            "contract.pdf",
        )
        self.assertEqual(self.client.get("/api/documents").json(), [document])

    def test_rejects_non_pdf_upload(self):
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("notes.txt", b"not a PDF", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Only PDF files are supported")

    def test_rejects_oversized_pdf_without_leaving_a_partial_file(self):
        with patch.object(settings, "MAX_FILE_SIZE_MB", 1):
            response = self.client.post(
                "/api/documents/upload",
                files={
                    "file": (
                        "large.pdf",
                        b"x" * (1024 * 1024 + 1),
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds 1MB", response.json()["detail"])
        self.assertEqual(list(self.upload_dir.glob("*.pdf")), [])

    def test_imports_existing_json_metadata(self):
        metadata_path = self.upload_dir / "docs_meta.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "document-1": {
                        "id": "document-1",
                        "name": "existing.pdf",
                        "size_mb": 0.1,
                        "pages": 1,
                        "chunks": 1,
                        "created_at": "2025-01-01T00:00:00+00:00",
                    }
                }
            ),
            encoding="utf-8",
        )
        store = DocumentStore(
            "sqlite:///:memory:",
            legacy_metadata_path=str(metadata_path),
        )
        self.addCleanup(store.close)

        self.assertEqual(store.get_document("document-1")["id"], "document-1")

    def test_existing_database_documents_migrate_without_becoming_public(self):
        database_path = self.upload_dir / "legacy.sqlite"
        engine = create_engine(URL.create("sqlite", database=str(database_path)))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE documents ("
                    "id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
                    "size_mb FLOAT NOT NULL, pages INTEGER NOT NULL, "
                    "chunks INTEGER NOT NULL, created_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO documents "
                    "(id, name, size_mb, pages, chunks, created_at) "
                    "VALUES ('legacy-1', 'legacy.pdf', 0.1, 1, 1, "
                    "'2025-01-01T00:00:00+00:00')"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE users ("
                    "id VARCHAR PRIMARY KEY, email VARCHAR NOT NULL UNIQUE, "
                    "password_hash VARCHAR NOT NULL, created_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES ('legacy-user', 'legacy@example.com', 'legacy-hash', "
                    "'2025-01-01T00:00:00+00:00')"
                )
            )
        engine.dispose()

        database_url = URL.create(
            "sqlite",
            database=str(database_path),
        ).render_as_string(hide_password=False)
        store = DocumentStore(database_url)
        self.addCleanup(store.close)
        user = store.create_user("new-owner@example.com", "not-a-real-hash")

        self.assertEqual(store.get_document("legacy-1")["name"], "legacy.pdf")
        self.assertEqual(store.list_documents(user["id"]), [])
        legacy_user = store.get_user_by_email("legacy@example.com")
        self.assertFalse(legacy_user["email_verified"])
        self.assertEqual(legacy_user["token_version"], 0)

    def test_reports_gemini_embedding_quota_as_rate_limit(self):
        with patch.object(
            main.rag_service,
            "add_chunks",
            side_effect=RuntimeError(
                "Error embedding content (RESOURCE_EXHAUSTED): 429"
            ),
        ):
            response = self.client.post(
                "/api/documents/upload",
                files={
                    "file": (
                        "contract.pdf",
                        make_pdf("The contract renews each year."),
                        "application/pdf",
                    )
                },
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"],
            "Document processing is temporarily busy due to high demand. "
            "Please wait 60 seconds and try uploading again, or try a smaller PDF.",
        )
        self.assertNotIn("Gemini", response.json()["detail"])
        self.assertNotIn("https://", response.json()["detail"])
        self.assertEqual(list(self.upload_dir.glob("*.pdf")), [])

    def test_query_returns_citations_and_rejects_blank_question(self):
        self.add_document("document-1", self.user["id"])
        result = {
            "answer": "The contract renews annually.",
            "sources": [
                {
                    "document_name": "contract.pdf",
                    "document_id": "document-1",
                    "page_number": 1,
                    "chunk_text": "The contract renews each year.",
                    "score": 0.9,
                }
            ],
            "has_sufficient_context": True,
        }
        with patch.object(main.rag_service, "query", return_value=result) as query:
            response = self.client.post(
                "/api/query", json={"question": "When does it renew?"}
            )
            blank_response = self.client.post("/api/query", json={"question": "  "})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.assertEqual(blank_response.status_code, 400)
        query.assert_called_once_with(
            "When does it renew?",
            doc_ids=["document-1"],
            k=settings.TOP_K,
        )

    def test_users_cannot_list_delete_or_query_another_users_documents(self):
        other_user = self.document_store.create_user(
            "other@example.com",
            hash_password("another-password-123"),
        )
        self.add_document("private-document", other_user["id"])
        with patch.object(main.rag_service, "query") as query:
            listed_docs = self.client.get("/api/documents")
            delete_response = self.client.delete("/api/documents/private-document")
            query_response = self.client.post(
                "/api/query",
                json={
                    "question": "Can I see this?",
                    "doc_ids": ["private-document"],
                },
            )

        self.assertEqual(listed_docs.json(), [])
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(query_response.status_code, 404)
        query.assert_not_called()
        self.assertIsNotNone(
            self.document_store.get_document("private-document", other_user["id"])
        )

    def test_delete_removes_file_metadata_and_indexed_chunks(self):
        pdf = make_pdf("The contract renews each year.")
        with patch.object(main.rag_service, "add_chunks", return_value=1):
            upload_response = self.client.post(
                "/api/documents/upload",
                files={"file": ("contract.pdf", pdf, "application/pdf")},
            )
        document = upload_response.json()
        stored_file = self.upload_dir / f"{document['id']}.pdf"
        self.assertTrue(stored_file.exists())

        with patch.object(main.rag_service, "delete_doc") as delete_doc:
            response = self.client.delete(f"/api/documents/{document['id']}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"deleted": document["id"]})
        delete_doc.assert_called_once_with(document["id"])
        self.assertFalse(stored_file.exists())
        self.assertEqual(self.client.get("/api/documents").json(), [])


if __name__ == "__main__":
    unittest.main()
