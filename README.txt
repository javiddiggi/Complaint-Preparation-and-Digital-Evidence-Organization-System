# Cyber Assist

**Cybercrime complaint preparation and evidence organization**

Cyber Assist is a local web application that helps users organize digital evidence and prepare cybercrime complaints. It is built from a static HTML/CSS/JavaScript frontend and a Python FastAPI backend, with SQLite persistence, ChromaDB retrieval, local Sentence Transformers embeddings, optional OpenAI assistance, and Tesseract OCR.

> [!WARNING]
> This is a **demonstration project**. It has no real authentication system. Do **not** enter real personal, financial, or case evidence. See [Security Notes](#security-notes).

## Table of Contents

- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start-windows-powershell)
- [Features and Workflow](#features-and-workflow)
- [Persistence and Privacy](#persistence-and-privacy)
- [Security Notes](#security-notes)
- [RAG Knowledge Base](#rag-knowledge-base)
- [API and Tests](#api-and-tests)
- [Known Limitations](#known-limitations)

## Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Static HTML, CSS, JavaScript (served by `node serve.js`) |
| Backend | Python, FastAPI |
| Database | SQLite |
| Retrieval (RAG) | ChromaDB, local Sentence Transformers embeddings |
| OCR | Tesseract |
| AI assistance | Optional OpenAI (local complaint template used otherwise) |

## Quick Start (Windows PowerShell)

### Prerequisites

- Python 3.10+
- Node.js
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract), installed separately if not already present

### Setup

1. **Create the Python environment** (once, from the project root):

   ```powershell
   python -m venv backend\.venv
   backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   ```

2. **Configure local settings:**

   ```powershell
   Copy-Item .env.example .env
   ```

   Then edit `.env` locally:

   - Keep `OPENAI_API_KEY` blank to use local complaint drafting.
   - The installed Tesseract path is configured by default in `.env.example`.
   - Set `KNOWLEDGE_BASE_ADMIN_KEY` to a long random value **only** if you use the protected knowledge-base ingestion API.
   - **Never commit `.env` or share its contents.**

3. **Start the frontend** in one terminal:

   ```powershell
   node serve.js
   ```

   Open <http://127.0.0.1:8000/>.

4. **Start the backend** in another terminal, from the project root:

   ```powershell
   backend\.venv\Scripts\uvicorn.exe main:app --app-dir backend --host 127.0.0.1 --port 8001
   ```

5. **Use the app.** Click **Get started**, then create and save an incident case. API docs are available at <http://127.0.0.1:8001/docs> while the backend is running.

### Notes

- The frontend is static and needs **no** `npm install` or build step.
- To use a different Tesseract path, set `TESSERACT_CMD` in the root `.env` file. The OCR module reads that file when imported, so restart the backend to apply changes.

## Features and Workflow

- **Demo sign-up/login:** Signup, login, logout, and dashboard navigation are demonstration-only. No credentials are verified and no server-side account or session exists. Each entered email selects a local random workspace scope.
- **Incident cases:** Incident fields synchronize to SQLite. Browser case IDs are mapped to server incident IDs within the current workspace scope.
- **Evidence upload:** Accepts PDF, PNG, JPG/JPEG, and TXT files up to 10 MB.
  - PDF/TXT extraction uses PyMuPDF and text decoding.
  - Images and scanned PDFs use Tesseract OCR when installed and configured.
  - OCR limits: 20 PDF pages, 20 megapixels, and 8,000 pixels per image dimension.
- **Review candidates:** Extracted facts and generated timeline items are labeled as review candidates and include evidence/source references. Timeline edits, missing-information results, complaint drafts, and guidance search results persist in SQLite.
- **Complaint drafts:** Drafts can be edited and exported as TXT or PDF.
  - The local template uses recorded incident fields, confirmed evidence facts, and source-linked timeline/evidence tables. It does not fill missing values with placeholders.
  - Missing items are listed separately; absent timeline or evidence is stated plainly.
  - PDF export formats the saved editable draft and includes page numbers.
  - The local template works without an OpenAI key. AI extraction and AI complaint drafting each require separate, explicit consent.
  - **Review and correct every draft before submission.**
- **Guidance search:** Displays retrieved source excerpts with title, issuing authority, URL, jurisdiction, source type, publication date (when known), and page/character reference.
  - Searches default to **India-only** guidance, and an India-scoped query excludes non-India sources.
  - US-specific IC3 summaries are selected only by an explicit US/FBI/IC3 query and are not Indian reporting instructions.
  - Retrieved text is source content, not a generated legal answer.

## Persistence and Privacy

- **Data locations:** `backend\cyber_assist.db`, `backend\uploads`, and `backend\chroma_db`. Do **not** remove these directories to reset the app.
- **SQLite stores:** incident details, evidence metadata and extracted text, reviewed information, timeline entries, complaint drafts, and saved guidance.
- **Uploaded files** are stored under `backend\uploads`.
- **OpenAI key:** `OPENAI_API_KEY` is read only by the backend from the root `.env`. The frontend, API responses, and application logs do not return the key.
- **External sharing:** Evidence is sent externally only after explicit consent for that operation. AI-generated facts remain candidates until the user confirms them. A local complaint template is used when OpenAI is not configured or available.
- **Local embeddings:** RAG embeddings do not send document or case text to a hosted embedding service. Only manifest-listed source documents should be ingested; never ingest case evidence into the shared knowledge base.

## Security Notes

The app has **no real account or authentication system**.

- The email entered at login selects a per-browser demo workspace namespace and is not verified.
- Browser case data and activity history use separate `localStorage` namespaces, and the matching scope is sent as the `X-Client-ID` header.
- API records are filtered by this client-supplied workspace scope to prevent accidental cross-demo display. This is **not** authenticated ownership or a security boundary.
- A user can impersonate another scope by changing client-side state or API headers.
- Legacy database rows remain intact, but records without a matching scope are intentionally not listed or readable through the scoped API. Do not assign their ownership without verifying who they belong to.
- A production deployment requires real authentication, server-issued sessions/tokens, and authorization checks derived from the authenticated principal.

## RAG Knowledge Base

Verified Indian official sources are listed in `backend\knowledge_base\sources.example.json` and described in `backend\knowledge_base\README.txt`.

**Included sources:**

- **CERT-In:** original Section 70B Directions (2022-04-28), FAQ, timeline extension (2022-06-27), the Directions page, and earlier CERT-In guidance PDFs.
- **I4C:** NCRP overview, FAQ, cybercrime categories, and NCTAU advisory (2026-08-26).
- **IC3 FAQ text files:** labeled separately as United States application summaries, not Indian reporting procedure.

**Not indexed:** The cybercrime.gov.in citizen manuals and FAQ could not be retrieved. Their URLs returned a generic HTML application shell instead of the requested content. They are marked `manual_ingestion_required` and are not claimed as indexed. The I4C pages were retrieved and indexed.

### Re-ingesting documents

Re-ingest all documents listed by the manifest, from the project root:

```powershell
backend\.venv\Scripts\python.exe backend\ingest_knowledge.py --documents backend\knowledge_base --manifest backend\knowledge_base\sources.example.json
```

### Configuration

- ChromaDB uses `backend\chroma_db` by default. Override with `CYBER_ASSIST_CHROMA_PATH`.
- Sentence Transformers runs locally on CPU. The model is downloaded on first use and cached by Hugging Face.

## API and Tests

The FastAPI application exposes:

| Endpoint | Purpose |
| --- | --- |
| `/health` | Health check |
| `/incidents` | Incident cases |
| `/evidence` | Evidence upload and metadata |
| `/guidance` | Guidance search |
| `/knowledge-base` | Knowledge-base ingestion (protected by admin key) |
| Workflow endpoints | Text/OCR extraction, information review, timeline generation, missing-information checks, complaint editing, and export |

See <http://127.0.0.1:8001/docs> for the live OpenAPI schema.

### Run backend tests (from the project root)

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

### Check frontend JavaScript syntax

```powershell
node --check js\app.js
```

The tests use synthetic records and documents. They do not delete or reset the existing SQLite database, upload files, or persistent Chroma collection.

## Known Limitations

- **Live OpenAI generation** requires a valid key, model access, and available API quota/credits. The last live check returned HTTP 429 (insufficient quota), so successful live generation is **not verified**. The tested local complaint fallback remains available when the provider is unavailable.
- **OCR** depends on a working Tesseract installation and language data.
- Neither OpenAI generation nor OCR behavior should be inferred from mocked tests alone.
