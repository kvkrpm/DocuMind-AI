from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
import uuid
from typing import TypedDict

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    String,
    case,
    create_engine,
    delete,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class DocumentInfo(TypedDict):
    id: str
    name: str
    size_mb: float
    pages: int
    chunks: int
    created_at: str


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    size_mb: Mapped[float] = mapped_column(Float, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    chunks: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String, nullable=True)

    def to_dict(self) -> DocumentInfo:
        created_at = self.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return {
            "id": self.id,
            "name": self.name,
            "size_mb": self.size_mb,
            "pages": self.pages,
            "chunks": self.chunks,
            "created_at": created_at.isoformat(),
        }


class StoreMigration(Base):
    __tablename__ = "document_store_migrations"

    id: Mapped[str] = mapped_column(String, primary_key=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    window: Mapped[int] = mapped_column(Integer, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuestSession(Base):
    __tablename__ = "guest_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DocumentStore:
    def __init__(
        self,
        database_url: str,
        legacy_metadata_path: str | None = None,
    ):
        if database_url.startswith("sqlite"):
            if ":memory:" in database_url:
                self.engine = create_engine(
                    database_url,
                    connect_args={"check_same_thread": False},
                    poolclass=StaticPool,
                )
            else:
                self.engine = create_engine(
                    database_url,
                    connect_args={"check_same_thread": False},
                )
        else:
            self.engine = create_engine(database_url)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._ensure_user_auth_columns()
        self._ensure_owner_column()
        self._ensure_guest_session_columns()
        if legacy_metadata_path is not None:
            self._import_legacy_metadata(legacy_metadata_path)

    def _ensure_owner_column(self) -> None:
        columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("documents")
        }
        with self.engine.begin() as connection:
            if "owner_id" not in columns:
                connection.execute(
                    text("ALTER TABLE documents ADD COLUMN owner_id VARCHAR")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_owner_id "
                    "ON documents (owner_id)"
                )
            )

    def _ensure_user_auth_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("users")}
        with self.engine.begin() as connection:
            if "email_verified" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN email_verified "
                        "BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
            if "token_version" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN token_version "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                )

    def _ensure_guest_session_columns(self) -> None:
        columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("guest_sessions")
        }
        if "document_count" in columns:
            return
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE guest_sessions ADD COLUMN document_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            connection.execute(
                text(
                    "UPDATE guest_sessions SET document_count = "
                    "(SELECT COUNT(*) FROM documents "
                    "WHERE documents.owner_id = guest_sessions.id)"
                )
            )

    def _import_legacy_metadata(self, path: str) -> None:
        migration_id = "legacy-json-metadata-v1"
        if not os.path.isfile(path):
            return

        with open(path, "r", encoding="utf-8") as metadata_file:
            metadata = json.load(metadata_file)

        with self.session_factory.begin() as session:
            if session.get(StoreMigration, migration_id):
                return
            for document in metadata.values():
                if session.get(Document, document["id"]) is None:
                    session.add(
                        Document(
                            id=document["id"],
                            name=document["name"],
                            size_mb=document["size_mb"],
                            pages=document["pages"],
                            chunks=document["chunks"],
                            created_at=datetime.fromisoformat(document["created_at"]),
                        )
                    )
            session.add(StoreMigration(id=migration_id))

    def create_user(
        self,
        email: str,
        password_hash: str,
        email_verified: bool = False,
    ) -> dict:
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=password_hash,
            created_at=datetime.now(timezone.utc),
            email_verified=email_verified,
            token_version=0,
        )
        with self.session_factory.begin() as session:
            session.add(user)
        return {
            "id": user.id,
            "email": user.email,
            "email_verified": user.email_verified,
            "token_version": user.token_version,
        }

    def get_user_by_email(self, email: str) -> dict | None:
        with self.session_factory() as session:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "password_hash": user.password_hash,
                "email_verified": user.email_verified,
                "token_version": user.token_version,
            }

    def get_user(self, user_id: str) -> dict | None:
        with self.session_factory() as session:
            user = session.get(User, user_id)
            if user is None:
                return None
            return {
                "id": user.id,
                "email": user.email,
                "email_verified": user.email_verified,
                "token_version": user.token_version,
            }

    def create_auth_token(
        self,
        token_hash: str,
        user_id: str,
        purpose: str,
        expires_at: datetime,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            session.execute(
                delete(AuthToken).where(
                    AuthToken.user_id == user_id,
                    AuthToken.purpose == purpose,
                    AuthToken.consumed_at.is_(None),
                )
            )
            session.add(
                AuthToken(
                    token_hash=token_hash,
                    user_id=user_id,
                    purpose=purpose,
                    expires_at=expires_at,
                    created_at=now,
                )
            )

    def consume_auth_token(self, token_hash: str, purpose: str) -> str | None:
        now = datetime.now(timezone.utc)
        statement = (
            update(AuthToken)
            .where(
                AuthToken.token_hash == token_hash,
                AuthToken.purpose == purpose,
                AuthToken.consumed_at.is_(None),
                AuthToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(AuthToken.user_id)
        )
        with self.session_factory.begin() as session:
            return session.scalar(statement)

    def mark_email_verified(self, user_id: str) -> dict | None:
        with self.session_factory.begin() as session:
            user = session.get(User, user_id)
            if user is None:
                return None
            user.email_verified = True
            return {
                "id": user.id,
                "email": user.email,
                "email_verified": user.email_verified,
                "token_version": user.token_version,
            }

    def reset_user_password(self, user_id: str, password_hash: str) -> bool:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            result = session.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    password_hash=password_hash,
                    token_version=User.token_version + 1,
                )
            )
            if not result.rowcount:
                return False
            session.execute(delete(AuthToken).where(AuthToken.user_id == user_id))
            return True

    def consume_auth_attempt(
        self,
        scope: str,
        client_key: str,
        window_seconds: int,
    ) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        current_window = int(now.timestamp()) // window_seconds
        rate_key = hashlib.sha256(
            f"{scope}\0{client_key}".encode("utf-8")
        ).hexdigest()
        if self.engine.dialect.name == "sqlite":
            insert = sqlite_insert
        elif self.engine.dialect.name == "postgresql":
            insert = postgresql_insert
        else:
            raise RuntimeError(
                f"Auth rate limiting is unsupported for {self.engine.dialect.name}"
            )

        statement = insert(AuthRateLimit).values(
            key=rate_key,
            window=current_window,
            hits=1,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[AuthRateLimit.key],
            set_={
                "window": current_window,
                "hits": case(
                    (AuthRateLimit.window == current_window, AuthRateLimit.hits + 1),
                    else_=1,
                ),
                "updated_at": now,
            },
        ).returning(AuthRateLimit.hits)

        with self.session_factory.begin() as session:
            hits = session.scalar(statement)

        retry_after = window_seconds - int(now.timestamp()) % window_seconds
        return int(hits), retry_after

    def create_guest_session(self, lifetime_hours: int) -> tuple[str, datetime]:
        now = datetime.now(timezone.utc)
        guest_id = f"guest_{uuid.uuid4()}"
        expires_at = now + timedelta(hours=lifetime_hours)
        with self.session_factory.begin() as session:
            session.add(
                GuestSession(
                    id=guest_id,
                    created_at=now,
                    expires_at=expires_at,
                    document_count=0,
                )
            )
        return guest_id, expires_at

    def get_active_guest_session(self, guest_id: str) -> bool:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            guest_session = session.get(GuestSession, guest_id)
            if guest_session is None:
                return False
            expires_at = guest_session.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            return expires_at > now

    def list_expired_guest_sessions(self) -> list[str]:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(GuestSession.id).where(GuestSession.expires_at <= now)
                ).all()
            )

    def delete_guest_session(self, guest_id: str) -> bool:
        with self.session_factory.begin() as session:
            result = session.execute(
                delete(GuestSession).where(GuestSession.id == guest_id)
            )
            return bool(result.rowcount)

    def reserve_guest_document(self, guest_id: str, max_docs: int) -> bool:
        now = datetime.now(timezone.utc)
        statement = (
            update(GuestSession)
            .where(
                GuestSession.id == guest_id,
                GuestSession.expires_at > now,
                GuestSession.document_count < max_docs,
            )
            .values(document_count=GuestSession.document_count + 1)
            .returning(GuestSession.id)
        )
        with self.session_factory.begin() as session:
            return session.scalar(statement) is not None

    def release_guest_document(self, guest_id: str) -> None:
        with self.session_factory.begin() as session:
            session.execute(
                update(GuestSession)
                .where(
                    GuestSession.id == guest_id,
                    GuestSession.document_count > 0,
                )
                .values(document_count=GuestSession.document_count - 1)
            )

    def count_documents(self, owner_id: str) -> int:
        with self.session_factory() as session:
            return (
                session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.owner_id == owner_id)
                )
                or 0
            )

    def list_documents(self, owner_id: str) -> list[DocumentInfo]:
        with self.session_factory() as session:
            documents = session.scalars(
                select(Document)
                .where(Document.owner_id == owner_id)
                .order_by(Document.created_at, Document.id)
            ).all()
            return [document.to_dict() for document in documents]

    def get_document(
        self, document_id: str, owner_id: str | None = None
    ) -> DocumentInfo | None:
        with self.session_factory() as session:
            query = select(Document).where(Document.id == document_id)
            if owner_id is not None:
                query = query.where(Document.owner_id == owner_id)
            document = session.scalar(query)
            return document.to_dict() if document else None

    def add_document(self, document: DocumentInfo, owner_id: str) -> None:
        with self.session_factory.begin() as session:
            session.add(
                Document(
                    id=document["id"],
                    name=document["name"],
                    size_mb=document["size_mb"],
                    pages=document["pages"],
                    chunks=document["chunks"],
                    created_at=datetime.fromisoformat(document["created_at"]),
                    owner_id=owner_id,
                )
            )

    def delete_document(self, document_id: str, owner_id: str) -> bool:
        with self.session_factory.begin() as session:
            result = session.execute(
                delete(Document).where(
                    Document.id == document_id,
                    Document.owner_id == owner_id,
                )
            )
            if result.rowcount:
                session.execute(
                    update(GuestSession)
                    .where(
                        GuestSession.id == owner_id,
                        GuestSession.document_count > 0,
                    )
                    .values(document_count=GuestSession.document_count - 1)
                )
            return bool(result.rowcount)

    def close(self) -> None:
        self.engine.dispose()

    def check_connection(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))


document_store = DocumentStore(
    settings.DATABASE_URL,
    os.path.join(settings.UPLOAD_DIR, "docs_meta.json"),
)
