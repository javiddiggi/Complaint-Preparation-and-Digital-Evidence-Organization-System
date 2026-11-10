# 🛡️ Cyber Assist

### Cybercrime complaint preparation and evidence organization

Cyber Assist is a local web application that helps users organize digital evidence and prepare cybercrime complaints.

> [!WARNING]
> **Demonstration project.** There is no real authentication system. Do **not** enter real personal, financial, or case evidence. See [Security Notes](#-security-notes).

---

## 📑 Table of Contents

| # | Section |
| :-: | --- |
| 1 | [Overview](#-overview) |
| 2 | [Quick Start](#-quick-start-windows-powershell) |
| 3 | [Features and Workflow](#-features-and-workflow) |
| 4 | [Persistence and Privacy](#-persistence-and-privacy) |
| 5 | [Security Notes](#-security-notes) |
| 6 | [RAG Knowledge Base](#-rag-knowledge-base) |
| 7 | [API and Tests](#-api-and-tests) |
| 8 | [Known Limitations](#-known-limitations) |

---

## 📌 Overview

### Tech Stack

| Layer | Technology |
| --- | --- |
| Frontend | Static HTML, CSS, JavaScript (served by `node serve.js`) |
| Backend | Python, FastAPI |
| Database | SQLite |
| Retrieval (RAG) | ChromaDB with local Sentence Transformers embeddings |
| OCR | Tesseract |
| AI assistance | Optional OpenAI (a local complaint template is used otherwise) |

### Default Ports

| Service | URL |
| --- | --- |
| Frontend | <http://127.0.0.1:8000/> |
| Backend API | <http://127.0.0.1:8001/> |
| API docs (Swagger) | <http://127.0.0.1:8001/docs> |

---

## 🚀 Quick Start (Windows PowerShell)

### Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Required for the backend |
| Node.js | Required to serve the frontend |
| Tesseract OCR | Install separately if not already installed |

### Step 1: Create the Python environment

Run once from the project root:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### Step 2: Configure local settings

```powershell
Copy-Item .env.example .env
```

Then edit `.env` locally.

| Setting | What to do |
| --- | --- |
| `OPENAI_API_KEY` | Keep blank to use local complaint drafting |
| `TESSERACT_CMD` | Set only to use a different Tesseract path (the installed path is preconfigured in `.env.example`) |
| `KNOWLEDGE_BASE_ADMIN_KEY` | Set to a long random value **only** if using the protected knowledge-base ingestion API |

> [!CAUTION]
> Never commit `.env` or share its contents.

### Step 3: Start the frontend

In one terminal:

```powershell
node serve.js
```

Open <http://127.0.0.1:8000/>.

### Step 4: Start the backend

In another terminal, from the project root:

```powershell
backend\.venv\Scripts\uvicorn.exe main:app --app-dir backend --host 127.0.0.1 --port 8001
```

### Step 5: Use the app

Click **Get started**, then create and save an incident case. API docs are available at <http://127.0.0.1:8001/docs> while the backend is running.

### Good to Know

| Topic | Detail |
| --- | --- |
| Build step | None. The frontend is static and needs no `npm install` or build |
| Tesseract path changes | The OCR module reads `.env` when imported, so **restart the backend** to apply changes |

---

## ✨ Features and Workflow

### Workflow Summary

| Stage | What happens |
| --- | --- |
| 1. Sign in (demo) | The entered email selects a local random workspace scope |
| 2. Create incident | Incident fields sync to SQLite within the current workspace scope |
| 3. Upload evidence | PDF, PNG, JPG/JPEG, or TXT files are stored and text is extracted |
| 4. Review | Extracted facts and timeline items are shown as review candidates |
| 5. Check gaps | Missing-information results are generated and saved |
| 6. Draft complaint | Draft is edited, then exported as TXT or PDF |
| 7. Guidance | Search retrieves official source excerpts |

### Evidence Upload

| Item | Limit or behavior |
| --- | --- |
| Accepted types | PDF, PNG, JPG/JPEG, TXT |
| Maximum file size | 10 MB |
| PDF/TXT extraction | PyMuPDF and text decoding |
| Images and scanned PDFs | Tesseract OCR (when installed and configured) |
| OCR page limit | 20 PDF pages |
| OCR image size limit | 20 megapixels, 8,000 pixels per image dimension |

### Review Candidates

Extracted facts and generated timeline items are labeled as **review candidates** and include evidence/source references. Timeline edits, missing-information results, complaint drafts, and guidance search results persist in SQLite.

### Complaint Drafts

| Aspect | Behavior |
| --- | --- |
| Editing | Drafts can be edited in the app |
| Export formats | TXT or PDF |
| Template inputs | Recorded incident fields, confirmed evidence facts, source-linked timeline/evidence tables |
| Missing values | Not filled with placeholders; missing items are listed separately |
| Absent timeline/evidence | Stated plainly |
| PDF export | Formats the saved editable draft and includes page numbers |
| OpenAI key | Not required for the local template |
| AI extraction / AI drafting | Each requires separate, explicit consent |

> [!IMPORTANT]
> Review and correct every draft before submission.

### Guidance Search

Results display retrieved source excerpts with:

| Field | Included |
| --- | :-: |
| Title | ✅ |
| Issuing authority | ✅ |
| URL | ✅ |
| Jurisdiction | ✅ |
| Source type | ✅ |
| Publication date | ✅ when known |
| Page / character reference | ✅ |

| Search behavior | Detail |
| --- | --- |
| Default scope | India-only guidance |
| India-scoped query | Excludes non-India sources |
| US sources | IC3 summaries are selected only by an explicit US/FBI/IC3 query and are not Indian reporting instructions |
| Nature of results | Source content, not a generated legal answer |

---

## 🔒 Persistence and Privacy

### Data Locations

| Path | Contents |
| --- | --- |
| `backend\cyber_assist.db` | SQLite database |
| `backend\uploads` | Uploaded evidence files |
| `backend\chroma_db` | ChromaDB vector store |

> [!NOTE]
> Do not remove these directories to reset the app.

### What SQLite Stores

| Category | Stored |
| --- | :-: |
| Incident details | ✅ |
| Evidence metadata and extracted text | ✅ |
| Reviewed information | ✅ |
| Timeline entries | ✅ |
| Complaint drafts | ✅ |
| Saved guidance | ✅ |

### Privacy Controls

| Topic | Behavior |
| --- | --- |
| OpenAI key | Read only by the backend from the root `.env`. The frontend, API responses, and application logs do not return it |
| External sharing | Evidence is sent externally only after explicit consent for that operation |
| AI-generated facts | Remain candidates until the user confirms them |
| Fallback | A local complaint template is used when OpenAI is not configured or available |
| Local embeddings | RAG embeddings do not send document or case text to a hosted embedding service |
| Knowledge-base ingestion | Only manifest-listed source documents should be ingested. Never ingest case evidence into the shared knowledge base |

---

## ⚠️ Security Notes

The app has **no real account or authentication system**.

| Area | Current behavior | Production requirement |
| --- | --- | --- |
| Signup / login / logout | Demonstration-only; credentials are not verified and no server-side account or session exists | Real authentication |
| Workspace selection | The email selects a per-browser demo workspace namespace and is not verified | Server-issued sessions or tokens |
| Client storage | Case data and activity history use separate `localStorage` namespaces | n/a |
| Scope header | The matching scope is sent as `X-Client-ID` | Authorization derived from the authenticated principal |
| Record filtering | Filtered by a client-supplied scope to prevent accidental cross-demo display. **Not** a security boundary | Authenticated ownership |
| Impersonation | A user can impersonate another scope by changing client-side state or API headers | Server-side authorization checks |
| Legacy rows | Remain intact but are intentionally not listed or readable through the scoped API | Verify real ownership before assigning |

---

## 📚 RAG Knowledge Base

Verified Indian official sources are listed in `backend\knowledge_base\sources.example.json` and described in `backend\knowledge_base\README.txt`.

### Included Sources

| Source | Documents | Status |
| --- | --- | :-: |
| CERT-In | Section 70B Directions (2022-04-28), FAQ, timeline extension (2022-06-27), Directions page, earlier guidance PDFs | ✅ Indexed |
| I4C | NCRP overview, FAQ, cybercrime categories, NCTAU advisory (2026-08-26) | ✅ Indexed |
| IC3 FAQ text files | United States application summaries, labeled separately, not Indian reporting procedure | ✅ Indexed (US-labeled) |
| cybercrime.gov.in | Citizen manuals and FAQ | ❌ `manual_ingestion_required` |

> [!NOTE]
> The cybercrime.gov.in URLs returned a generic HTML application shell instead of the requested content, so they are not claimed as indexed.

### Re-ingest Documents

Re-ingest all documents listed by the manifest, from the project root:

```powershell
backend\.venv\Scripts\python.exe backend\ingest_knowledge.py --documents backend\knowledge_base --manifest backend\knowledge_base\sources.example.json
```

### Configuration

| Setting | Detail |
| --- | --- |
| ChromaDB path | `backend\chroma_db` by default; override with `CYBER_ASSIST_CHROMA_PATH` |
| Embeddings | Sentence Transformers, runs locally on CPU |
| Model download | On first use, cached by Hugging Face |

---

## 🧪 API and Tests

### API Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/health` | Health check |
| `/incidents` | Incident cases |
| `/evidence` | Evidence upload and metadata |
| `/guidance` | Guidance search |
| `/knowledge-base` | Knowledge-base ingestion |
| Workflow endpoints | Text/OCR extraction, information review, timeline generation, missing-information checks, complaint editing, and export |

See <http://127.0.0.1:8001/docs> for the live OpenAPI schema.

### Commands

| Task | Command (run from the project root) |
| --- | --- |
| Run backend tests | `backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v` |
| Check frontend JS syntax | `node --check js\app.js` |

The tests use synthetic records and documents. They do not delete or reset the existing SQLite database, upload files, or persistent Chroma collection.

---

## 🚧 Known Limitations

| Area | Limitation |
| --- | --- |
| Live OpenAI generation | Requires a valid key, model access, and available API quota/credits. The last live check returned HTTP 429 (insufficient quota), so successful live generation is **not verified** |
| Fallback | The tested local complaint fallback remains available when the provider is unavailable |
| OCR | Depends on a working Tesseract installation and language data |
| Test coverage | Neither OpenAI generation nor OCR behavior should be inferred from mocked tests alone |
