import hashlib
from functools import lru_cache
import os
from pathlib import Path
import re
from urllib.parse import urlparse
from typing import Any
from datetime import date


BACKEND_DIR = Path(__file__).resolve().parent
ALLOWED_SUFFIXES = {".pdf", ".txt"}
ALLOWED_HOSTS = (".gov", ".gov.in", "cert-in.org.in", "rbi.org.in")
SOURCE_TYPES = {
    "official_document",
    "official_web_page",
    "application_summary",
}
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150


class KnowledgeBaseError(RuntimeError):
    pass


def validate_official_source_url(source_url: str) -> str:
    parsed = urlparse(source_url.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not hostname
        or not any(hostname == suffix.lstrip(".") or hostname.endswith(suffix) for suffix in ALLOWED_HOSTS)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Source URL must use HTTPS on an approved official government, CERT-In, or RBI domain.")
    return parsed.geturl()


def _dependencies():
    try:
        from langchain_chroma import Chroma
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError as error:
        raise KnowledgeBaseError(
            "RAG dependencies are not installed. Install backend\\requirements.txt and retry."
        ) from error
    return Chroma, HuggingFaceEmbeddings, RecursiveCharacterTextSplitter


@lru_cache(maxsize=1)
def _embedding_function():
    _, HuggingFaceEmbeddings, _ = _dependencies()
    model_name = os.getenv(
        "SENTENCE_TRANSFORMER_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    try:
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError(
            "The local Sentence Transformers model could not be loaded. Check model access and SENTENCE_TRANSFORMER_MODEL."
        ) from error


@lru_cache(maxsize=1)
def _vector_store():
    Chroma, _, _ = _dependencies()
    configured_path = Path(os.getenv("CYBER_ASSIST_CHROMA_PATH", str(BACKEND_DIR / "chroma_db")))
    persist_directory = configured_path if configured_path.is_absolute() else (BACKEND_DIR.parent / configured_path).resolve()
    persist_directory.mkdir(parents=True, exist_ok=True)
    try:
        return Chroma(
            collection_name="cyberassist_official_guidance",
            embedding_function=_embedding_function(),
            persist_directory=str(persist_directory),
        )
    except KnowledgeBaseError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError("The local Chroma knowledge base could not be opened.") from error


def _load_documents(path: Path):
    try:
        if path.suffix.lower() == ".pdf":
            from langchain_community.document_loaders import PyMuPDFLoader

            return PyMuPDFLoader(str(path)).load()
        from langchain_community.document_loaders import TextLoader

        return TextLoader(str(path), encoding="utf-8").load()
    except UnicodeDecodeError:
        from langchain_community.document_loaders import TextLoader

        return TextLoader(str(path), encoding="cp1252").load()
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError("The PDF/TXT source document could not be read.") from error


def ingest_document(
    path: str | Path,
    source_name: str,
    publisher: str,
    source_url: str,
    publication_date: str | None = None,
    source_type: str = "official_document",
    jurisdiction: str = "unspecified",
) -> dict[str, Any]:
    file_path = Path(path).resolve()
    suffix = file_path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("Only PDF and TXT documents can be ingested.")
    if not file_path.is_file():
        raise ValueError("Source document was not found.")
    if file_path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("Source document exceeds the 10 MB limit.")
    if not source_name.strip() or len(source_name) > 512 or not publisher.strip() or len(publisher) > 255:
        raise ValueError("Source name is required and limited to 512 characters; publisher is required and limited to 255 characters.")
    official_url = validate_official_source_url(source_url)
    if source_type not in SOURCE_TYPES:
        raise ValueError("Source type must be official_document, official_web_page, or application_summary.")
    if len(jurisdiction) > 100 or not jurisdiction.strip():
        raise ValueError("Jurisdiction is required and limited to 100 characters.")
    if publication_date:
        try:
            publication_date = date.fromisoformat(publication_date).isoformat()
        except ValueError as error:
            raise ValueError("Publication date must use YYYY-MM-DD format.") from error
    _, _, RecursiveCharacterTextSplitter = _dependencies()
    documents = _load_documents(file_path)
    for document in documents:
        metadata = dict(document.metadata)
        page = metadata.get("page")
        metadata.update(
            {
                "source_name": source_name.strip(),
                "source_title": source_name.strip(),
                "publisher": publisher.strip(),
                "issuing_authority": publisher.strip(),
                "source_url": official_url,
                "document_name": file_path.name,
                "source_type": source_type,
                "jurisdiction": jurisdiction.strip(),
            }
        )
        if publication_date:
            metadata["publication_date"] = publication_date
        if page is not None:
            metadata["page"] = int(page)
            metadata["page_number"] = int(page) + 1
        else:
            metadata.pop("page", None)
            metadata.pop("page_number", None)
        document.metadata = metadata
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )
    chunks = [chunk for chunk in splitter.split_documents(documents) if chunk.page_content.strip()]
    if not chunks:
        raise KnowledgeBaseError("The source document contains no readable text.")
    document_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    ids = [
        hashlib.sha256(f"{document_digest}:{index}".encode()).hexdigest()
        for index in range(len(chunks))
    ]
    try:
        store = _vector_store()
        store.add_documents(chunks, ids=ids)
    except KnowledgeBaseError:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError("The document could not be embedded or saved to Chroma.") from error
    return {
        "source_name": source_name.strip(),
        "publisher": publisher.strip(),
        "source_url": official_url,
        "publication_date": publication_date,
        "source_type": source_type,
        "jurisdiction": jurisdiction.strip(),
        "chunks_ingested": len(chunks),
        "document_id": document_digest,
    }


def retrieve_guidance(
    query: str,
    limit: int = 5,
    incident_type: str | None = None,
    extracted_information: list[str] | None = None,
) -> list[dict[str, Any]]:
    if len(query.strip()) < 2:
        raise ValueError("Enter at least two characters to search guidance.")
    terms = [query.strip()]
    if incident_type and incident_type.strip():
        terms.append(f"Incident type: {incident_type.strip()[:120]}")
    if extracted_information:
        terms.extend(str(item).strip()[:500] for item in extracted_information[:20] if str(item).strip())
    search_text = "\n".join(terms)
    us_query = re.search(
        r"\b(?:united\s+states|u\.?s\.?a?\.?|ic3|fbi)\b",
        search_text,
        flags=re.IGNORECASE,
    ) is not None
    if not knowledge_base_status()["ready"]:
        return []
    store = _vector_store()
    try:
        jurisdiction = "United States" if us_query else "India"
        options = {"filter": {"jurisdiction": jurisdiction}}
        results = store.similarity_search_with_score(
            search_text,
            k=max(1, min(limit, 10)),
            **options,
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError("Semantic guidance retrieval failed.") from error
    passages = []
    for document, distance in results:
        metadata = document.metadata
        page_number = metadata.get("page_number")
        if page_number is None and str(metadata.get("document_name", "")).lower().endswith(".pdf"):
            page_number = int(metadata.get("page", 0)) + 1
        reference = f"page {page_number}" if page_number is not None else "text document"
        if metadata.get("start_index") is not None:
            reference += f", character {metadata['start_index']}"
        passages.append(
            {
                "text": document.page_content,
                "source_name": metadata.get("source_name") or metadata.get("document_name") or "Unknown source",
                "source_title": metadata.get("source_title") or metadata.get("source_name") or "Unknown source",
                "document_name": metadata.get("document_name") or "Unknown document",
                "publisher": metadata.get("publisher") or "Unknown publisher",
                "issuing_authority": metadata.get("issuing_authority") or metadata.get("publisher") or "Unknown publisher",
                "source_url": metadata.get("source_url") or "",
                "publication_date": metadata.get("publication_date") or None,
                "source_type": metadata.get("source_type") or "legacy_unclassified",
                "jurisdiction": metadata.get("jurisdiction") or "unspecified",
                "page_number": page_number,
                "reference": reference,
                "score": round(1.0 / (1.0 + max(float(distance), 0.0)), 4),
            }
        )
    return passages


def knowledge_base_status() -> dict[str, Any]:
    try:
        import chromadb
    except ImportError as error:
        raise KnowledgeBaseError(
            "RAG dependencies are not installed. Install backend\\requirements.txt and retry."
        ) from error
    configured_path = Path(os.getenv("CYBER_ASSIST_CHROMA_PATH", str(BACKEND_DIR / "chroma_db")))
    persist_directory = configured_path if configured_path.is_absolute() else (BACKEND_DIR.parent / configured_path).resolve()
    persist_directory.mkdir(parents=True, exist_ok=True)
    client = None
    try:
        client = chromadb.PersistentClient(path=str(persist_directory))
        collection = client.get_or_create_collection("cyberassist_official_guidance")
        count = collection.count()
        sources = collection.get(limit=min(count, 1000), include=["metadatas"])["metadatas"] if count else []
    except (OSError, ValueError, RuntimeError) as error:
        raise KnowledgeBaseError("The Chroma knowledge-base status could not be read.") from error
    finally:
        if client is not None:
            client.close()
    source_names = sorted(
        {str(metadata.get("source_name", "")) for metadata in sources if metadata.get("source_name")}
    )
    return {"ready": count > 0, "chunks": count, "documents": source_names}


def ingest_directory(directory: str | Path, source_manifest: str | Path) -> list[dict[str, Any]]:
    import json

    root = Path(directory).resolve()
    manifest_path = Path(source_manifest).resolve()
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The source manifest is not readable JSON.") from error
    if not isinstance(entries, dict):
        raise ValueError("The source manifest must be a JSON object.")

    document_entries = entries.get("documents", entries)
    if not isinstance(document_entries, dict):
        raise ValueError("The manifest documents value must map relative paths to source metadata.")

    ingested = []
    for relative_path, metadata in sorted(document_entries.items()):
        if not isinstance(relative_path, str) or not isinstance(metadata, dict):
            raise ValueError("Each manifest document must map a relative path to source metadata.")
        path = (root / relative_path).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"Document path escapes the knowledge-base directory: {relative_path}.")
        if path.suffix.lower() not in ALLOWED_SUFFIXES or not path.is_file():
            raise ValueError(f"Manifest document is missing or has an unsupported type: {relative_path}.")
        ingested.append(
            ingest_document(
                path,
                str(metadata.get("source_name", "")),
                str(metadata.get("publisher", "")),
                str(metadata.get("source_url", "")),
                metadata.get("publication_date"),
                str(metadata.get("source_type", "official_document")),
                str(metadata.get("jurisdiction", "unspecified")),
            )
        )
    return ingested
