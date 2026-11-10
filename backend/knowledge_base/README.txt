Cyber Assist local RAG knowledge base

Only manifest-listed files are ingested. Chroma stores locally generated
embeddings and source metadata in backend\chroma_db. Never add user evidence or
complaint data to this shared reference collection.

On 2026-11-04, eight additional verified sources added 132 chunks to the
existing collection (213 to 345 chunks). No collection reset was performed.

VERIFIED INDIA-SPECIFIC OFFICIAL SOURCES

CERT-In, Ministry of Electronics and Information Technology, Government of
India:

- Guidelines on Information Security Practices for Government Entities:
  https://www.cert-in.org.in/PDF/guidelinesgovtentities.pdf
  No publication date is stated in the document. Its stated audience is
  government entities; do not present it as an individual complaint procedure.
- 15 Elemental Cyber Defense Controls for Micro, Small, and Medium Enterprises
  (MSMEs), Version 1.0, dated 2025-09-01:
  https://www.cert-in.org.in/PDF/Elemental_Cyber_Defense_Controls_for_MSME.pdf
- Directions under sub-section (6) of section 70B of the Information Technology
  Act, 2000, dated 2022-04-28 (20 new chunks):
  https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf
- Frequently Asked Questions on Cyber Security Directions of 28.04.2022
  (66 new chunks):
  https://www.cert-in.org.in/PDF/FAQs_on_CyberSecurityDirections_May2022.pdf
  The PDF was retrieved and its text extracted; no publication date is asserted.
- Extension of timelines for enforcement of the 28.04.2022 Directions, dated
  2022-06-27 (7 new chunks):
  https://www.cert-in.org.in/PDF/CERT-In_directions_extension_MSMEs_and_validation_27.06.2022.pdf
- Directions by CERT-In under Section 70B, Information Technology Act 2000
  (visible content from the official page; 3 new chunks):
  https://www.cert-in.org.in/Directions70B.jsp

Indian Cyber Crime Coordination Centre (I4C), Ministry of Home Affairs,
Government of India:

- National Cybercrime Reporting Portal [NCRP] overview:
  https://i4c.mha.gov.in/ncrp.aspx (3 new chunks)
- FAQ (Frequently Asked Questions), including complaint reporting, financial
  cyber fraud reporting through 1930, complaint information, and Women/Child
  reporting options (15 new chunks):
  https://i4c.mha.gov.in/FAQ.aspx
- Cybercrime categories (10 new chunks):
  https://i4c.mha.gov.in/cyber-crime-categories.aspx
- National Cybercrime Threat Analytics Unit advisory, “Malicious Side Loaded
  Pornographic Android Apps leading to Unauthorized Financial Transactions,”
  dated 2026-08-26 (8 new chunks):
  https://i4c.mha.gov.in/theme/resources/advisories/ADVISORY-TAU-ADV-NIGHTPLAY-INSTAGRAM-ADS.pdf

The I4C pages were retrieved as HTML and their visible main content was
extracted to TXT. The PDFs were verified by their PDF signatures; titles,
issuing authorities, and dates (where stated) were checked against the PDF
text. PDF chunks retain one-based page citations. Web-page chunks cite text
character offsets. Unknown publication dates are omitted rather than inferred
from server or file timestamps.

The cybercrime.gov.in portal page and the three requested resources below
remain manual-ingestion items: their URLs returned a generic HTML application
shell instead of readable portal content or the requested PDF/FAQ content.
They were not indexed:

- https://cybercrime.gov.in/
- https://cybercrime.gov.in/UploadMedia/MHA-CitizenManualReportOtherCyberCrime-v10.pdf
- https://cybercrime.gov.in/UploadMedia/MHA-CitizenManualReportCPRGRcomplaints-v10.pdf
- https://cybercrime.gov.in/webform/FAQ.aspx

The I4C pages above are indexed and are not marked as inaccessible. Recheck
the portal URLs later, download only when the actual PDF/page content is
retrievable, then verify it and update sources.example.json.

The indexed I4C FAQ lists basic complaint fields and says to provide necessary
supporting information, but it does not provide an itemized citizen evidence
checklist. The CERT-In government-entities guidance includes a direction not
to tamper with potential evidence; its stated audience is government entities
and it is not a citizen-specific evidence manual.

All Indian sources are metadata-tagged as jurisdiction India and as official
documents or official web pages. By default, RAG search returns Indian sources
only. US-specific reference summaries are retrieved only when a query
explicitly names the United States, US, FBI, or IC3.

SEPARATELY LABELED UNITED STATES REFERENCES

The two files in seed_documents are Cyber Assist summaries of the FBI Internet
Crime Complaint Center (IC3) FAQ, not official source text. They remain labeled
as United States application summaries and are never treated as Indian
reporting procedure. The original source is:
https://www.ic3.gov/Home/FAQ

INGESTION

From the project root, ingest all manifest-listed source files:
backend\.venv\Scripts\python.exe backend\ingest_knowledge.py --documents backend\knowledge_base --manifest backend\knowledge_base\sources.example.json

The CLI validates HTTPS source URLs against approved official government,
CERT-In, and RBI domains. Supported source files are PDF and UTF-8/Windows-1252
TXT up to 10 MB. Chunks are embedded locally using Sentence Transformers.
Deterministic chunk IDs make repeat ingestion safe and prevent duplicate chunks.

The protected API is also available when KNOWLEDGE_BASE_ADMIN_KEY is set in the
root .env file. POST a PDF or TXT multipart upload to
http://127.0.0.1:8001/knowledge-base/ingest with X-KB-Admin-Key and the form
fields source_name, publisher, source_url, source_type, jurisdiction, and
publication_date when known. Check GET /knowledge-base/status after ingestion.

Retrieved citations include document title, issuing authority, official URL,
jurisdiction, source type, publication date when known, and PDF page or text
character reference. Verify current instructions with the issuing authority;
retrieval is not legal advice.
