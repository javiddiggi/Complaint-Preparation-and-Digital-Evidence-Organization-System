Cyber Assist — cybercrime complaint preparation and evidence organization

This project is an existing local HTML/CSS/JavaScript frontend with a Python
FastAPI backend, SQLite persistence, ChromaDB retrieval, local Sentence
Transformers embeddings, optional OpenAI assistance, and Tesseract OCR.

QUICK START (Windows PowerShell)

1. Install Python 3.10+ and Node.js. Install Tesseract OCR separately if it is
   not already installed.
2. From the project root, create the Python environment once if needed:
   python -m venv backend\.venv
   backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
3. Configure local settings:
   Copy-Item .env.example .env
   Edit .env locally. Keep OPENAI_API_KEY blank to use local complaint drafting.
   The installed Tesseract path is configured by default in .env.example.
   Set KNOWLEDGE_BASE_ADMIN_KEY to a long random value only if using the
   protected knowledge-base ingestion API. Never commit .env or share its
   contents.
4. Start the frontend in one terminal:
   node serve.js
   Open http://127.0.0.1:8000/
5. Start the backend in another terminal from the project root:
   backend\.venv\Scripts\uvicorn.exe main:app --app-dir backend --host 127.0.0.1 --port 8001
6. Use Get started, then create and save an incident case. The API docs are at
   http://127.0.0.1:8001/docs while the backend is running.

The frontend is static and needs no npm install/build step. To use a different
Tesseract path, set TESSERACT_CMD in the root .env file. The OCR module reads
that file when imported; restarting the backend applies configuration changes.

PERSISTENCE AND PRIVACY

- Existing data is in backend\cyber_assist.db, backend\uploads, and
  backend\chroma_db. Do not remove these directories to reset the app.
- SQLite stores incident details, evidence metadata and extracted text, reviewed
  information, timeline entries, complaint drafts, and saved guidance.
- Uploaded evidence files are stored under backend\uploads. The app has no real
  account/authentication system. Signup/login are demonstration-only; the email
  selects a per-browser demo workspace namespace and is not verified. API
  records are filtered by a client-supplied workspace scope to prevent
  accidental cross-demo display, but this is not authenticated ownership or a
  security boundary. Do not put real personal, financial, or case evidence into
  this demonstration.
- OPENAI_API_KEY is read only by the backend from the root .env. The frontend,
  API responses, and application logs do not return the key. Evidence is sent
  externally only after explicit consent for that operation. AI-generated
  facts remain candidates until the user confirms them. A local complaint
  template is used when OpenAI is not configured or available.
- Local RAG embeddings do not send document or case text to a hosted embedding
  service. Only manifest-listed source documents should be ingested; never
  ingest case evidence into the shared knowledge base.

WORKFLOW

- Signup/login/logout and dashboard navigation are demonstration-only; no
  credentials are verified and no server-side user account or session exists.
  Each entered email selects a local random workspace scope. Browser case data
  and activity history use separate localStorage namespaces, and the matching
  scope is sent as X-Client-ID. A user can impersonate another scope by changing
  client-side state or API headers; production deployment requires real
  authentication, server-issued sessions/tokens, and authorization checks
  derived from the authenticated principal.
- Incident fields synchronize to SQLite. Browser case IDs are mapped to
  server incident IDs within the current workspace scope. Legacy rows remain
  intact, but records without a matching scope are intentionally not listed or
  readable through the scoped API; do not assign their ownership without
  verifying who they belong to.
- Evidence accepts PDF, PNG, JPG/JPEG, and TXT files up to 10 MB. PDF/TXT
  extraction uses PyMuPDF/text decoding; images and scanned PDFs use actual
  Tesseract OCR when installed and configured. OCR is limited to 20 PDF pages,
  20 megapixels, and 8,000 pixels per image dimension.
- Extracted facts and generated timeline items are labeled as review candidates
  and include evidence/source references. Timeline edits, missing-information
  results, complaint drafts, and guidance search results persist in SQLite.
- Complaint drafts can be edited and exported as TXT or PDF. The local template
  uses recorded incident fields, confirmed evidence facts, and source-linked
  timeline/evidence tables without filling missing values with placeholders.
  Missing items are listed separately; absent timeline/evidence are stated
  plainly. PDF export formats the saved editable draft and includes page
  numbers. Review and correct every draft before submission. The local template
  is available without an OpenAI key. AI extraction and AI complaint drafting
  require separate, explicit consent.
- Guidance search displays retrieved source excerpts with the title, issuing
  authority, URL, jurisdiction, source type, publication date when known, and
  page/character reference. An India-scoped query excludes non-India sources.
Searches default to India-only guidance; US-specific IC3 summaries are
selected only by an explicit US/FBI/IC3 query and are not Indian reporting
instructions. Retrieved text is source content, not a generated legal answer.

RAG KNOWLEDGE BASE

The verified Indian official sources are listed in
backend\knowledge_base\sources.example.json and described in
backend\knowledge_base\README.txt. The collection includes CERT-In's original
Section 70B Directions (2022-04-28), FAQ, timeline extension (2022-06-27), and
Directions page; I4C's NCRP overview, FAQ, cybercrime categories, and NCTAU
advisory (2026-08-26), in addition to the earlier CERT-In guidance PDFs.

The requested cybercrime.gov.in citizen manuals and FAQ could not be retrieved:
their URLs returned a generic HTML application shell rather than the requested
content. They are marked manual_ingestion_required and are not claimed as
indexed. The I4C pages were retrieved and indexed. The IC3 FAQ text files
remain separately labeled United States application summaries, not Indian
reporting procedure.

Re-ingest all documents listed by the manifest from the project root:
backend\.venv\Scripts\python.exe backend\ingest_knowledge.py --documents backend\knowledge_base --manifest backend\knowledge_base\sources.example.json

Chroma uses backend\chroma_db by default, configurable with
CYBER_ASSIST_CHROMA_PATH. Sentence Transformers runs locally on CPU; its model
is downloaded on first use and cached by Hugging Face.

API AND TESTS

The FastAPI application exposes /health, /incidents, /evidence, /guidance,
/knowledge-base, and workflow endpoints for text/OCR extraction, information
review, timeline generation, missing-information checks, complaint editing,
and export. See http://127.0.0.1:8001/docs for the live OpenAPI schema.

Run backend tests from the project root:
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v

Check frontend JavaScript syntax:
node --check js\app.js

The tests use synthetic records/documents and do not delete or reset the
existing SQLite database, upload files, or persistent Chroma collection.
Live OpenAI generation requires a valid key, model access, and available API
quota/credits. The last live check returned HTTP 429 for insufficient quota;
successful live generation is not verified. The tested local complaint
fallback remains available when the provider is unavailable. Actual OCR
depends on a working Tesseract installation and language data. Neither should
be inferred from mocked tests alone.
