import re
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address")
        return email


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return AuthRequest.normalize_email(value)


class TokenRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class PasswordResetRequest(TokenRequest):
    password: str = Field(min_length=12, max_length=128)


class DocumentInfo(BaseModel):
    id: str
    name: str
    size_mb: float
    pages: int
    chunks: int
    created_at: str

class QueryRequest(BaseModel):
    question: str = Field(max_length=2000)
    doc_ids: Optional[List[str]] = None

class SourceCitation(BaseModel):
    document_name: str
    document_id: str
    page_number: int
    chunk_text: str
    score: float

class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceCitation]
    has_sufficient_context: bool
