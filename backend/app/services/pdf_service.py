from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.config import settings
import uuid

def extract_pdf_with_pages(
    file_path: str,
    max_pages: int | None = None,
    max_chars: int | None = None,
):
    reader = PdfReader(file_path)
    if max_pages is not None and len(reader.pages) > max_pages:
        raise ValueError(f"PDF exceeds the {max_pages}-page limit")
    pages_data = []
    extracted_chars = 0
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            extracted_chars += len(text)
            if max_chars is not None and extracted_chars > max_chars:
                raise ValueError("PDF contains too much extractable text")
            pages_data.append({"page_number": i+1, "text": text})
    return pages_data, len(reader.pages)

def chunk_pages(pages_data, doc_id, doc_name):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    all_chunks = []
    for page in pages_data:
        chunks = splitter.split_text(page["text"])
        for c in chunks:
            all_chunks.append({
                "text": c,
                "metadata": {
                    "document_id": doc_id,
                    "document_name": doc_name,
                    "page_number": page["page_number"],
                    "chunk_id": str(uuid.uuid4())
                }
            })
    return all_chunks

def validate_file(file_size: int, filename: str):
    if not filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF files are supported")
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        raise ValueError(f"File exceeds {settings.MAX_FILE_SIZE_MB}MB limit")
