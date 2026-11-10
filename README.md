# Cyber Assist — Cybercrime Complaint Preparation and Evidence Organization

## 1. Project Overview

Cyber Assist is a local educational project that helps users organize digital evidence and prepare cybercrime complaints.

It uses a static HTML/CSS/JavaScript frontend and a Python FastAPI backend, with SQLite persistence, ChromaDB retrieval, local Sentence Transformers embeddings, optional OpenAI assistance, and Tesseract OCR.

This is a demonstration project with no real authentication. Do not enter real personal, financial, or case evidence.

## 2. Project Components

- `serve.js` — Serves the static frontend at `http://127.0.0.1:8000/`.
- `js/app.js` — Frontend application logic.
- `backend/main.py` — FastAPI application, served on port 8001.
- `backend/requirements.txt` — Python dependencies.
- `backend/cyber_assist.db` — SQLite database for incidents, evidence, timelines, drafts, and guidance.
- `backend/uploads/` — Uploaded evidence files.
- `backend/chroma_db/` — ChromaDB vector store for guidance retrieval.
- `backend/knowledge_base/` — Official guidance sources and the `sources.example.json` manifest.
- `backend/ingest_knowledge.py` — Ingests manifest-listed documents into ChromaDB.
- `backend/tests/` — Backend unit tests.
- `.env.example` — Template for local settings.

## 3. Requirements

- Python 3.10 or later
- Node.js
- Tesseract OCR, installed separately if not already present
- Windows PowerShell (the commands below use Windows paths)

## 4. Setup and Run

### 4.1 Create the Python environment

Run once from the project root:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### 4.2 Configure local settings

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally:

- `OPENAI_API_KEY` — Keep blank to use local complaint drafting.
- `TESSERACT_CMD` — Set only to use a different Tesseract path. The installed path is preconfigured in `.env.example`.
- `KNOWLEDGE_BASE_ADMIN_KEY` — Set to a long random value only if using the protected knowledge-base ingestion API.

Never commit `.env` or share its contents.

### 4.3 Start the frontend

In one terminal:

```powershell
node serve.js
```

Open `http://127.0.0.1:8000/`. The frontend is static and needs no `npm install` or build step.

### 4.4 Start the backend

In another terminal, from the project root:

```powershell
backend\.venv\Scripts\uvicorn.exe main:app --app-dir backend --host 127.0.0.1 --port 8001
```

API documentation is available at `http://127.0.0.1:8001/docs` while the backend is running.

### 4.5 Use the app

Click **Get started**, then create and save an incident case.

If you change the Tesseract path in `.env`, restart the backend. The OCR module reads that file when imported.

## 5. Features

- Incident cases — Incident fields synchronize to SQLite within the current workspace scope.
- Evidence upload — Accepts PDF, PNG, JPG/JPEG, and TXT files up to 10 MB.
- Text extraction — PDF and TXT files use PyMuPDF and text decoding. Images and scanned PDFs use Tesseract OCR when installed and configured.
- OCR limits — 20 PDF pages, 20 megapixels, and 8,000 pixels per image dimension.
- Review candidates — Extracted facts and generated timeline items are labeled as review candidates with evidence/source references.
- Persistence — Timeline edits, missing-information results, complaint drafts, and guidance search results are saved in SQLite.
- Complaint drafts — Editable, and exported as TXT or PDF. PDF export includes page numbers.
- Guidance search — Retrieves official source excerpts from the knowledge base.

## 6. Complaint Drafting

The local template uses recorded incident fields, confirmed evidence facts, and source-linked timeline and evidence tables.

- Missing values are not filled with placeholders. Missing items are listed separately.
- Absent timeline or evidence is stated plainly.
- The local template works without an OpenAI key.
- AI extraction and AI complaint drafting each require separate, explicit consent.
- AI-generated facts remain candidates until the user confirms them.

Review and correct every draft before submission.

## 7. Guidance Search

Results show the title, issuing authority, URL, jurisdiction, source type, publication date (when known), and page or character reference.

- Searches default to India-only guidance.
- An India-scoped query excludes non-India sources.
- US-specific IC3 summaries are selected only by an explicit US, FBI, or IC3 query. They are not Indian reporting instructions.
- Retrieved text is source content, not a generated legal answer.

## 8. RAG Knowledge Base

Verified Indian official sources are listed in `backend/knowledge_base/sources.example.json` and described in `backend/knowledge_base/README.txt`.

- CERT-In — Section 70B Directions (2022-04-28), FAQ, timeline extension (2022-06-27), Directions page, and earlier guidance PDFs.
- I4C — NCRP overview, FAQ, cybercrime categories, and NCTAU advisory (2026-08-26).
- IC3 FAQ text files — Labeled separately as United States application summaries.
- cybercrime.gov.in citizen manuals and FAQ — Not indexed. The URLs returned a generic HTML application shell instead of the content, so they are marked `manual_ingestion_required`.

To re-ingest all manifest-listed documents, run from the project root:

```powershell
backend\.venv\Scripts\python.exe backend\ingest_knowledge.py --documents backend\knowledge_base --manifest backend\knowledge_base\sources.example.json
```

ChromaDB uses `backend\chroma_db` by default, configurable with `CYBER_ASSIST_CHROMA_PATH`. Sentence Transformers runs locally on CPU. The model is downloaded on first use and cached by Hugging Face.

Only manifest-listed source documents should be ingested. Never ingest case evidence into the shared knowledge base.

## 9. Privacy and Data Storage

- `backend\cyber_assist.db` — SQLite database.
- `backend\uploads` — Uploaded evidence files.
- `backend\chroma_db` — Vector store.

Do not remove these directories to reset the app.

- `OPENAI_API_KEY` is read only by the backend from the root `.env`. The frontend, API responses, and application logs do not return it.
- Evidence is sent externally only after explicit consent for that operation.
- A local complaint template is used when OpenAI is not configured or available.
- RAG embeddings do not send document or case text to a hosted embedding service.

## 10. Security Limitations

The app has no real account or authentication system.

- Signup, login, logout, and dashboard navigation are demonstration-only. No credentials are verified and no server-side account or session exists.
- The entered email selects a local random workspace scope. Browser case data and activity history use separate `localStorage` namespaces, and the scope is sent as the `X-Client-ID` header.
- Records are filtered by this client-supplied scope to prevent accidental cross-demo display. It is not authenticated ownership or a security boundary.
- A user can impersonate another scope by changing client-side state or API headers.
- Legacy rows without a matching scope are intentionally not listed or readable through the scoped API. Do not assign their ownership without verifying who they belong to.
- A production deployment requires real authentication, server-issued sessions or tokens, and authorization checks derived from the authenticated principal.

## 11. API Endpoints

- `/health` — Health check.
- `/incidents` — Incident cases.
- `/evidence` — Evidence upload and metadata.
- `/guidance` — Guidance search.
- `/knowledge-base` — Knowledge-base ingestion.
- Workflow endpoints — Text/OCR extraction, information review, timeline generation, missing-information checks, complaint editing, and export.

See `http://127.0.0.1:8001/docs` for the live OpenAPI schema.

## 12. Testing

Run from the project root:

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
node --check js\app.js
```

- The first command runs the backend tests.
- The second checks frontend JavaScript syntax.

The tests use synthetic records and documents. They do not delete or reset the existing SQLite database, upload files, or persistent Chroma collection.

## 13. Known Limitations

- Live OpenAI generation requires a valid key, model access, and available API quota or credits. The last live check returned HTTP 429 (insufficient quota), so successful live generation is not verified.
- The tested local complaint fallback remains available when the provider is unavailable.
- OCR depends on a working Tesseract installation and language data.
- Neither OpenAI generation nor OCR behavior should be inferred from mocked tests alone.
