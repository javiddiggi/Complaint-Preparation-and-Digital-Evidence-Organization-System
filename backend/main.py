from datetime import datetime, timezone
import hmac
import os
from pathlib import Path, PurePosixPath
import tempfile
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, HTTPException, status
from fastapi import File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ai_services import (
    AIConfigurationError,
    AIProviderError,
    AIResponseError,
    build_complaint_context,
    extract_evidence_facts,
    fallback_notice,
    generate_complaint_with_ai,
)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from database import (
    Base,
    engine,
    ensure_evidence_extraction_schema,
    ensure_workflow_schema,
    get_db,
)
from models import Evidence, Incident
from ocr import extract_evidence_ocr
from schemas import (
    EvidenceCategory,
    EvidenceDeleteResponse,
    EvidenceRead,
    EvidenceTextRead,
    ComplaintUpdate,
    EvidenceFactsReview,
    ExternalAIConsent,
    GuidanceSearch,
    IncidentCreate,
    IncidentDeleteResponse,
    IncidentMutationResponse,
    IncidentRead,
    IncidentUpdate,
    TimelineUpdate,
)
from text_extraction import extract_evidence_text
from workflow import (
    build_complaint_draft,
    detect_missing_information,
    extract_structured_information,
    generate_timeline_candidates,
    render_complaint_pdf,
)
from rag import (
    KnowledgeBaseError,
    ingest_document,
    knowledge_base_status,
    retrieve_guidance as retrieve_semantic_guidance,
)


Base.metadata.create_all(bind=engine)
ensure_evidence_extraction_schema()
ensure_workflow_schema()
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"
MAX_EVIDENCE_SIZE = 10 * 1024 * 1024
EVIDENCE_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
}
CATEGORIES = {
    "Communication",
    "Financial or transaction",
    "Website or URL",
    "Incident document",
    "Other",
}

app = FastAPI(
    title="Cyber Assist API",
    description="Cybercrime Complaint Preparation System",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "Cyber Assist Backend is running!"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/knowledge-base/status")
def get_knowledge_base_status():
    try:
        return knowledge_base_status()
    except KnowledgeBaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/knowledge-base/ingest")
async def ingest_knowledge_document(
    file: UploadFile = File(...),
    source_name: str = Form(..., max_length=512),
    publisher: str = Form(..., max_length=255),
    source_url: str = Form(..., max_length=2048),
    publication_date: str | None = Form(default=None, max_length=10),
    source_type: str = Form(default="official_document", max_length=64),
    jurisdiction: str = Form(default="unspecified", max_length=100),
    admin_key: str | None = Header(default=None, alias="X-KB-Admin-Key"),
):
    expected_key = os.getenv("KNOWLEDGE_BASE_ADMIN_KEY", "").strip()
    if not expected_key or not admin_key or not hmac.compare_digest(admin_key, expected_key):
        raise HTTPException(status_code=403, detail="Knowledge-base ingestion is not authorized.")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".txt"}:
        raise HTTPException(status_code=400, detail="Only PDF and TXT documents can be ingested.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Knowledge document exceeds the 10 MB limit.")
    try:
        with tempfile.TemporaryDirectory(prefix="cyberassist-kb-") as directory:
            path = Path(directory) / f"{uuid4().hex}{suffix}"
            path.write_bytes(content)
            result = ingest_document(
                path,
                source_name,
                publisher,
                source_url,
                publication_date,
                source_type,
                jurisdiction,
            )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KnowledgeBaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    finally:
        await file.close()
    return result


def find_incident(
    db: Session,
    incident_id: str,
    client_id: str | None = None,
) -> Incident | None:
    if not client_id:
        return None
    return db.scalar(
        select(Incident).where(
            Incident.client_id == client_id,
            (Incident.id == incident_id) | (Incident.client_case_id == incident_id),
        )
    )


def require_demo_scope(client_id: str | None) -> str:
    if not client_id or len(client_id) > 64:
        raise HTTPException(status_code=403, detail="A demo workspace scope is required.")
    return client_id


def evidence_or_404(evidence_id: str, db: Session, client_id: str | None) -> Evidence:
    scope = require_demo_scope(client_id)
    record = db.scalar(
        select(Evidence)
        .join(Incident, Evidence.incident_id == Incident.id)
        .where(Evidence.id == evidence_id, Incident.client_id == scope)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return record


@app.post(
    "/incidents",
    response_model=IncidentMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_incident(
    incident: IncidentCreate,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
    client_case_id: str | None = Header(default=None, alias="X-Client-Case-ID"),
):
    client_id = require_demo_scope(client_id)
    record = None
    if client_id and client_case_id:
        record = find_incident(db, client_case_id, client_id)
    if record is None:
        record = Incident(
            **incident.model_dump(),
            client_id=client_id,
            client_case_id=client_case_id,
        )
        db.add(record)
    else:
        for field, value in incident.model_dump(exclude_unset=True).items():
            setattr(record, field, value)
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"message": "Incident details saved successfully", "incident": record}


@app.get("/incidents", response_model=list[IncidentRead])
def list_incidents(
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    scope = require_demo_scope(client_id)
    return db.scalars(
        select(Incident)
        .where(Incident.client_id == scope)
        .order_by(Incident.created_at.desc())
    ).all()


@app.get("/incidents/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = find_incident(db, incident_id, client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return record


@app.put("/incidents/{incident_id}", response_model=IncidentMutationResponse)
def update_incident(
    incident_id: str,
    incident: IncidentUpdate,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = find_incident(db, incident_id, client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    for field, value in incident.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    db.commit()
    db.refresh(record)
    return {"message": "Incident details updated successfully", "incident": record}


@app.delete(
    "/incidents/{incident_id}",
    response_model=IncidentDeleteResponse,
)
def delete_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = find_incident(db, incident_id, client_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    evidence_paths = [evidence_path(item.stored_filename) for item in record.evidence_items]
    db.delete(record)
    db.commit()
    for path in evidence_paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise HTTPException(
                status_code=500, detail="Incident was deleted, but an evidence file could not be removed."
            ) from error
    return {"message": "Incident deleted successfully", "id": incident_id}


def evidence_path(stored_filename: str) -> Path:
    directory = UPLOADS_DIR.resolve()
    path = (directory / stored_filename).resolve()
    if path.parent != directory:
        raise HTTPException(status_code=500, detail="Evidence storage path is invalid.")
    return path


@app.post(
    "/incidents/{incident_id}/evidence",
    response_model=EvidenceRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_evidence(
    incident_id: str,
    file: UploadFile = File(...),
    category: EvidenceCategory = Form(...),
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = find_incident(db, incident_id, client_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    filename = file.filename or ""
    original_filename = PurePosixPath(filename.replace("\\", "/")).name
    extension = PurePosixPath(original_filename).suffix.lower()
    if not original_filename or len(original_filename) > 255 or extension not in EVIDENCE_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported evidence filename or file extension.")
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    expected_type = EVIDENCE_MIME_TYPES[extension]
    if content_type != expected_type:
        raise HTTPException(
            status_code=400,
            detail=f"File type does not match the .{extension[1:]} extension. Expected {expected_type}.",
        )
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unsupported evidence category.")

    evidence_id = str(uuid4())
    stored_filename = evidence_id + extension
    temporary_path = evidence_path(evidence_id + ".uploading")
    destination_path = evidence_path(stored_filename)
    file_size = 0
    header = bytearray()
    try:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("xb") as destination:
            while chunk := await file.read(64 * 1024):
                file_size += len(chunk)
                if file_size > MAX_EVIDENCE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Evidence file exceeds the 10 MB limit.",
                    )
                if len(header) < 8:
                    header.extend(chunk[: 8 - len(header)])
                destination.write(chunk)

        if file_size == 0:
            raise HTTPException(status_code=400, detail="Evidence file is empty.")
        expected_signature = {
            ".pdf": b"%PDF-",
            ".png": b"\x89PNG\r\n\x1a\n",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
        }.get(extension)
        if expected_signature and not header.startswith(expected_signature):
            raise HTTPException(
                status_code=400,
                detail=f"File contents do not match the .{extension[1:]} extension.",
            )

        record = Evidence(
            id=evidence_id,
            incident_id=incident.id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_type=content_type,
            file_size=file_size,
            category=category,
        )
        db.add(record)
        db.flush()
        temporary_path.replace(destination_path)
        db.commit()
        db.refresh(record)
        return record
    except HTTPException:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise
    except SQLAlchemyError as error:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Evidence metadata could not be saved.") from error
    except OSError as error:
        db.rollback()
        temporary_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Evidence file could not be stored.") from error
    finally:
        await file.close()


@app.get("/incidents/{incident_id}/evidence", response_model=list[EvidenceRead])
def list_evidence(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = find_incident(db, incident_id, client_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return db.scalars(
        select(Evidence)
        .where(Evidence.incident_id == incident.id)
        .order_by(Evidence.uploaded_at.desc())
    ).all()


@app.get("/evidence/{evidence_id}", response_model=EvidenceRead)
def get_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    return evidence_or_404(evidence_id, db, client_id)


def evidence_text_response(record: Evidence) -> dict:
    return {
        "evidence_id": record.id,
        "status": record.extraction_status,
        "extracted_text": record.extracted_text,
        "message": record.extraction_message,
        "extracted_at": record.extracted_at,
    }


def workflow_data(record: Incident) -> dict:
    return dict(record.workflow_data or {})


def save_workflow_data(record: Incident, data: dict, db: Session) -> None:
    record.workflow_data = data
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="Incident workflow data could not be saved.") from error


def incident_or_404(
    incident_id: str,
    db: Session,
    client_id: str | None = None,
) -> Incident:
    incident = find_incident(db, incident_id, client_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


def evidence_for_incident(db: Session, incident_id: str) -> list[Evidence]:
    return db.scalars(
        select(Evidence)
        .where(Evidence.incident_id == incident_id)
        .order_by(Evidence.uploaded_at.asc())
    ).all()


@app.post("/evidence/{evidence_id}/extract-information")
def extract_evidence_information(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    evidence = evidence_or_404(evidence_id, db, client_id)
    if evidence.extraction_status not in {"extracted", "ocr_extracted"} or not evidence.extracted_text:
        raise HTTPException(
            status_code=409,
            detail="Extract readable text from this evidence before extracting information.",
        )
    evidence.structured_data = extract_structured_information(
        evidence.extracted_text,
        evidence.id,
        evidence.original_filename,
    )
    try:
        db.commit()
        db.refresh(evidence)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="Extracted information could not be saved.") from error
    return {
        "evidence_id": evidence.id,
        "structured_data": evidence.structured_data,
        "message": "Candidate facts were copied from extracted text. Review them against the evidence.",
    }


@app.post("/evidence/{evidence_id}/extract-information/ai")
def extract_evidence_information_with_ai(
    evidence_id: str,
    request: ExternalAIConsent,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    evidence = evidence_or_404(evidence_id, db, client_id)
    if evidence.extraction_status not in {"extracted", "ocr_extracted"} or not evidence.extracted_text:
        raise HTTPException(
            status_code=409,
            detail="Extract readable text from this evidence before AI information extraction.",
        )
    if not request.consent_external_ai:
        raise HTTPException(status_code=400, detail="Explicit consent is required before sending evidence text to OpenAI.")
    try:
        structured_data = extract_evidence_facts(
            evidence.extracted_text,
            evidence.id,
            evidence.original_filename,
        )
    except AIConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AIProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except AIResponseError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    evidence.structured_data = structured_data
    try:
        db.commit()
        db.refresh(evidence)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="AI-extracted information could not be saved.") from error
    return {
        "evidence_id": evidence.id,
        "structured_data": evidence.structured_data,
        "message": structured_data["notice"],
    }


@app.patch("/evidence/{evidence_id}/structured-information")
def review_evidence_facts(
    evidence_id: str,
    request: EvidenceFactsReview,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    evidence = evidence_or_404(evidence_id, db, client_id)
    structured_data = dict(evidence.structured_data or {})
    facts = list(structured_data.get("facts") or [])
    indices = set(request.confirmed_fact_indices)
    if any(index < 0 or index >= len(facts) for index in indices):
        raise HTTPException(status_code=422, detail="One or more selected fact indices are invalid.")
    for index, fact in enumerate(facts):
        fact = dict(fact)
        fact["review_status"] = "confirmed" if index in indices else "candidate"
        facts[index] = fact
    structured_data["facts"] = facts
    evidence.structured_data = structured_data
    try:
        db.commit()
        db.refresh(evidence)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="Fact review could not be saved.") from error
    return {"evidence_id": evidence.id, "structured_data": evidence.structured_data}


@app.get("/incidents/{incident_id}/timeline")
def get_timeline(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    return (incident.workflow_data or {}).get("timeline", {"events": [], "generated_at": None})


@app.post("/incidents/{incident_id}/generate-timeline")
def generate_timeline(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    evidence = evidence_for_incident(db, incident.id)
    existing = workflow_data(incident)
    user_events = [
        event for event in existing.get("timeline", {}).get("events", [])
        if event.get("origin") == "user"
    ]
    events = user_events + generate_timeline_candidates(evidence, incident)
    existing["timeline"] = {
        "events": events,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notice": "Evidence-derived events are candidates and require review; dates are not inferred when ambiguous.",
    }
    save_workflow_data(incident, existing, db)
    return existing["timeline"]


@app.put("/incidents/{incident_id}/timeline")
def update_timeline(
    incident_id: str,
    request: TimelineUpdate,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    evidence_ids = {item.id for item in evidence_for_incident(db, incident.id)}
    for event in request.events:
        if event.evidence_id and event.evidence_id not in evidence_ids:
            raise HTTPException(status_code=422, detail="Timeline event references evidence not linked to this incident.")
    existing = workflow_data(incident)
    existing["timeline"] = {
        "events": [event.model_dump(mode="json") for event in request.events],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_workflow_data(incident, existing, db)
    return existing["timeline"]


@app.get("/incidents/{incident_id}/missing-information")
def get_missing_information(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    prompts = detect_missing_information(incident, evidence_for_incident(db, incident.id))
    data = workflow_data(incident)
    data["missing_information"] = {
        "items": prompts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_workflow_data(incident, data, db)
    return data["missing_information"]


@app.post("/incidents/{incident_id}/generate-complaint")
def generate_complaint(
    incident_id: str,
    db: Session = Depends(get_db),
    request: ExternalAIConsent | None = Body(default=None),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    evidence = evidence_for_incident(db, incident.id)
    data = workflow_data(incident)
    prompts = detect_missing_information(incident, evidence)
    events = data.get("timeline", {}).get("events", [])
    method = "local_template"
    source_refs = []
    narrative_override = None
    if isinstance(request, ExternalAIConsent) and request.consent_external_ai:
        try:
            ai_draft = generate_complaint_with_ai(build_complaint_context(incident, evidence, events))
            narrative_override = ai_draft["text"]
            method = ai_draft["method"]
            source_refs = ai_draft["source_refs"]
            notice = ai_draft["notice"]
        except (AIConfigurationError, AIProviderError, AIResponseError) as error:
            notice = fallback_notice(error)
    else:
        notice = (
            "Local template used. No case data was sent externally. "
            "Draft facts are not independently verified. "
            "To request AI drafting, explicitly consent on the complaint page."
        )
    draft = build_complaint_draft(
        incident,
        evidence,
        events,
        prompts,
        narrative_override=narrative_override,
    )
    data["missing_information"] = {
        "items": prompts,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    data["complaint"] = {
        "text": draft,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reviewed": False,
        "method": method,
        "source_refs": source_refs,
        "notice": notice,
    }
    save_workflow_data(incident, data, db)
    return data["complaint"]


@app.get("/incidents/{incident_id}/complaint")
def get_complaint(
    incident_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    return (incident.workflow_data or {}).get("complaint", {"text": "", "reviewed": False})


@app.put("/incidents/{incident_id}/complaint")
def update_complaint(
    incident_id: str,
    request: ComplaintUpdate,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    data = workflow_data(incident)
    current = data.get("complaint", {})
    data["complaint"] = {
        **current,
        "text": request.text,
        "reviewed": request.reviewed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_workflow_data(incident, data, db)
    return data["complaint"]


@app.get("/incidents/{incident_id}/complaint/export")
def export_complaint(
    incident_id: str,
    format: str = "txt",
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    complaint = (incident.workflow_data or {}).get("complaint", {})
    content = complaint.get("text", "")
    if not content.strip():
        raise HTTPException(status_code=409, detail="Generate or save a complaint draft before exporting.")
    if format.lower() == "txt":
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="cyber-assist-complaint.txt"'},
        )
    if format.lower() != "pdf":
        raise HTTPException(status_code=400, detail="Export format must be txt or pdf.")

    pdf_content = render_complaint_pdf(content)
    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="cyber-assist-complaint.pdf"'},
    )


@app.get("/guidance/search")
def search_guidance(query: str, limit: int = 5):
    if len(query.strip()) < 2:
        raise HTTPException(status_code=422, detail="Enter at least two characters to search guidance.")
    try:
        results = retrieve_semantic_guidance(query, max(1, min(limit, 10)))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KnowledgeBaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"query": query, "results": results, "method": "chroma_sentence_transformers"}


@app.post("/incidents/{incident_id}/guidance")
def retrieve_incident_guidance(
    incident_id: str,
    request: GuidanceSearch,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    incident = incident_or_404(incident_id, db, client_id)
    evidence_records = evidence_for_incident(db, incident.id)
    extracted_information = [
        f"{fact.get('type', '')}: {fact.get('value', '')}"
        for item in evidence_records
        for fact in (item.structured_data or {}).get("facts", [])
        if fact.get("review_status") in {"candidate", "confirmed"}
    ]
    try:
        results = retrieve_semantic_guidance(
            request.query,
            incident_type=incident.incident_type,
            extracted_information=extracted_information,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KnowledgeBaseError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    data = workflow_data(incident)
    data["guidance"] = {
        "query": request.query,
        "results": results,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "method": "chroma_sentence_transformers",
    }
    save_workflow_data(incident, data, db)
    return data["guidance"]


@app.post("/evidence/{evidence_id}/extract-text", response_model=EvidenceTextRead)
def extract_evidence_text_endpoint(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = evidence_or_404(evidence_id, db, client_id)
    if record.extraction_status == "ocr_extracted":
        return evidence_text_response(record)

    extension = Path(record.original_filename).suffix.lower()
    result = extract_evidence_text(evidence_path(record.stored_filename), extension)
    record.extracted_text = result.extracted_text
    record.extraction_status = result.status
    record.extraction_message = result.message
    record.extracted_at = datetime.now(timezone.utc)
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="Text extraction results could not be saved.") from error
    return evidence_text_response(record)


@app.post("/evidence/{evidence_id}/ocr", response_model=EvidenceTextRead)
def run_evidence_ocr(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = evidence_or_404(evidence_id, db, client_id)

    if record.extraction_status in {"extracted", "ocr_extracted"}:
        return evidence_text_response(record)

    extension = Path(record.original_filename).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg", ".pdf"}:
        raise HTTPException(
            status_code=400,
            detail="OCR is supported for PNG, JPG, JPEG, and scanned PDF evidence.",
        )

    result = extract_evidence_ocr(evidence_path(record.stored_filename), extension)
    record.extracted_text = result.extracted_text
    record.extraction_status = result.status
    record.extraction_message = result.message
    record.extracted_at = datetime.now(timezone.utc)
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(status_code=500, detail="OCR results could not be saved.") from error
    return evidence_text_response(record)


@app.get("/evidence/{evidence_id}/text", response_model=EvidenceTextRead)
def get_evidence_text(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    return evidence_text_response(evidence_or_404(evidence_id, db, client_id))


@app.get("/evidence/{evidence_id}/download")
def download_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = evidence_or_404(evidence_id, db, client_id)
    path = evidence_path(record.stored_filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return FileResponse(
        path,
        media_type=record.file_type,
        filename=record.original_filename,
    )


@app.delete("/evidence/{evidence_id}", response_model=EvidenceDeleteResponse)
def delete_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    client_id: str | None = Header(default=None, alias="X-Client-ID"),
):
    record = evidence_or_404(evidence_id, db, client_id)
    path = evidence_path(record.stored_filename)
    db.delete(record)
    db.commit()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise HTTPException(
            status_code=500, detail="Evidence metadata was deleted, but its file could not be removed."
        ) from error
    return {"message": "Evidence deleted successfully", "id": evidence_id}
