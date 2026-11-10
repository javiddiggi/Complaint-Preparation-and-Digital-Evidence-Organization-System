import tempfile
import unittest
from pathlib import Path
import sys
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf as fitz
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import ocr
from database import Base
from models import Evidence, Incident
from ocr import OcrResult
from ai_services import AIConfigurationError, AIProviderError
from schemas import (
    ComplaintUpdate,
    EvidenceFactsReview,
    ExternalAIConsent,
    GuidanceSearch,
    IncidentCreate,
    TimelineEventInput,
    TimelineUpdate,
)
from workflow import (
    detect_missing_information,
    extract_structured_information,
    generate_timeline_candidates,
    retrieve_guidance,
)


class OcrExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.png_path = self.root / "synthetic.png"
        image = Image.new("RGB", (640, 180), "white")
        ImageDraw.Draw(image).text((24, 48), "Synthetic OCR test 12345", fill="black")
        image.save(self.png_path)
        self.jpg_path = self.root / "screenshot.jpg"
        image.save(self.jpg_path, quality=95)
    def tearDown(self):
        self.directory.cleanup()

    def test_synthetic_png_and_jpg_are_preprocessed_and_recognized(self):
        with patch.object(ocr, "_tesseract_error", return_value=None), patch.object(
            ocr.pytesseract, "image_to_string", return_value="Synthetic OCR test 12345"
        ) as recognize:
            for path in (self.png_path, self.jpg_path):
                result = ocr.extract_evidence_ocr(path, path.suffix)
                self.assertEqual(result.status, "ocr_extracted")
                self.assertEqual(result.extracted_text, "Synthetic OCR test 12345")
            self.assertEqual(recognize.call_count, 2)

    def test_scanned_pdf_is_processed_page_by_page(self):
        pdf_path = self.root / "scan.pdf"
        document = fitz.open()
        page = document.new_page(width=320, height=180)
        page.insert_image(page.rect, stream=self.png_path.read_bytes())
        document.save(pdf_path)
        document.close()

        with patch.object(ocr, "_tesseract_error", return_value=None), patch.object(
            ocr.pytesseract, "image_to_string", return_value="Scanned page text"
        ) as recognize:
            result = ocr.extract_evidence_ocr(pdf_path, ".pdf")
        self.assertEqual(result.status, "ocr_extracted")
        self.assertEqual(result.extracted_text, "Scanned page text")
        recognize.assert_called_once()

    def test_empty_ocr_and_corrupted_files_are_reported(self):
        with patch.object(ocr, "_tesseract_error", return_value=None), patch.object(
            ocr.pytesseract, "image_to_string", return_value="   "
        ):
            result = ocr.extract_evidence_ocr(self.png_path, ".png")
        self.assertEqual(result.status, "empty")

        corrupt_png = self.root / "corrupt.png"
        corrupt_png.write_bytes(b"not an image")
        self.assertEqual(ocr.extract_evidence_ocr(corrupt_png, ".png").status, "error")

        corrupt_pdf = self.root / "corrupt.pdf"
        corrupt_pdf.write_bytes(b"not a PDF")
        self.assertEqual(ocr.extract_evidence_ocr(corrupt_pdf, ".pdf").status, "error")

    def test_missing_tesseract_is_reported_with_setup_guidance(self):
        with patch.object(
            ocr.pytesseract,
            "get_tesseract_version",
            side_effect=ocr.TesseractNotFoundError(),
        ):
            result = ocr.extract_evidence_ocr(self.png_path, ".png")
        self.assertEqual(result.status, "error")
        self.assertIn("TESSERACT_CMD", result.message)

    def test_image_and_pdf_processing_limits_are_enforced(self):
        oversized_image = Image.new("L", (8_001, 1))
        with self.assertRaisesRegex(ValueError, "dimensions exceed"):
            ocr._preprocess_image(oversized_image)

        pdf_path = self.root / "too-many-pages.pdf"
        document = fitz.open()
        for _ in range(ocr.MAX_OCR_PDF_PAGES + 1):
            document.new_page()
        document.save(pdf_path)
        document.close()
        result = ocr.extract_evidence_ocr(pdf_path, ".pdf")
        self.assertEqual(result.status, "error")
        self.assertIn("limited to 20 pages", result.message)


class OcrApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.client_id = "test-demo-user"
        self.incident_id = "incident-test"
        self.evidence_id = "evidence-test"
        with self.Session() as session:
            session.add(Incident(id=self.incident_id, client_id=self.client_id))
            session.add(
                Evidence(
                    id=self.evidence_id,
                    incident_id=self.incident_id,
                    original_filename="scan.png",
                    stored_filename="evidence-test.png",
                    file_type="image/png",
                    file_size=10,
                    category="Other",
                )
            )
            session.commit()

        self.original_uploads = main.UPLOADS_DIR
        main.UPLOADS_DIR = self.uploads
        (self.uploads / "evidence-test.png").write_bytes(b"test")

    def tearDown(self):
        main.UPLOADS_DIR = self.original_uploads
        self.engine.dispose()
        self.directory.cleanup()

    def test_ocr_text_and_timestamp_persist_after_refresh(self):
        with patch(
            "main.extract_evidence_ocr",
            return_value=OcrResult("ocr_extracted", "Saved OCR text", "OCR succeeded."),
        ):
            with self.Session() as session:
                response = main.run_evidence_ocr(self.evidence_id, session, self.client_id)
                self.assertEqual(response["extracted_text"], "Saved OCR text")
                self.assertIsNotNone(response["extracted_at"])

        with self.Session() as session:
            refreshed = main.get_evidence_text(self.evidence_id, session, self.client_id)
        self.assertEqual(refreshed["status"], "ocr_extracted")
        self.assertEqual(refreshed["extracted_text"], "Saved OCR text")
        self.assertEqual(refreshed["extracted_at"], response["extracted_at"])

    def test_existing_successful_extraction_is_not_overwritten(self):
        with self.Session() as session:
            record = session.get(Evidence, self.evidence_id)
            record.extraction_status = "extracted"
            record.extracted_text = "Original PDF/TXT extraction"
            session.commit()

        with patch("main.extract_evidence_ocr") as recognize:
            with self.Session() as session:
                response = main.run_evidence_ocr(self.evidence_id, session, self.client_id)
        self.assertEqual(response["extracted_text"], "Original PDF/TXT extraction")
        recognize.assert_not_called()

    def test_text_extraction_does_not_replace_saved_ocr_text(self):
        with self.Session() as session:
            record = session.get(Evidence, self.evidence_id)
            record.extraction_status = "ocr_extracted"
            record.extracted_text = "Saved OCR result"
            session.commit()

        with patch("main.extract_evidence_text") as extract_text:
            with self.Session() as session:
                response = main.extract_evidence_text_endpoint(self.evidence_id, session, self.client_id)
        self.assertEqual(response["extracted_text"], "Saved OCR result")
        extract_text.assert_not_called()

    def test_evidence_lookup_resolves_browser_case_alias(self):
        client_id = "synthetic-browser"
        local_case_id = "CA-LOCAL-TEST"
        with self.Session() as session:
            response = main.create_incident(
                IncidentCreate(incident_type="Phishing"),
                session,
                client_id=client_id,
                client_case_id=local_case_id,
            )
            incident_id = response["incident"].id
            self.assertNotEqual(incident_id, local_case_id)
            found = main.get_incident(local_case_id, session, client_id)
            self.assertEqual(found.id, incident_id)
            evidence = main.list_evidence(local_case_id, session, client_id)
            self.assertEqual(evidence, [])

    def test_structured_facts_and_timeline_keep_exact_sources(self):
        source_text = (
            "On 2026-10-12 at 14:30, I paid INR 5,000. "
            "Transaction ID: ABCD123456. I received a message from https://example.test."
        )
        data = extract_structured_information(source_text, "evidence-1", "receipt.txt")
        self.assertEqual(
            {fact["type"] for fact in data["facts"]},
            {"date", "time", "amount", "transaction_reference", "url"},
        )
        self.assertTrue(all(fact["source_excerpt"] in source_text for fact in data["facts"]))
        self.assertTrue(all(fact["review_status"] == "candidate" for fact in data["facts"]))

    def test_timeline_normalizes_meridiem_times_for_persistence(self):
        source_text = "On 2026-10-12 at 2:30 PM, the synthetic report was submitted."
        evidence = SimpleNamespace(
            id="evidence-time",
            original_filename="time.txt",
            extraction_status="extracted",
            extracted_text=source_text,
        )
        incident = SimpleNamespace(summary=None, incident_date=None, incident_time=None)
        event = generate_timeline_candidates([evidence], incident)[0]
        self.assertEqual(event["time"], "14:30")
        self.assertIn("2:30 PM", event["description"])
        self.assertEqual(TimelineEventInput(**event).time.isoformat(timespec="minutes"), "14:30")

    def test_timeline_extracts_time_only_conversation_messages_with_supported_date_and_source(self):
        source_text = (
            "Chat transcript\n"
            "12:15 PM - Customer: I reported the unauthorized payment.\n"
            "12:17 PM - Agent: We have blocked the card.\n"
            "12:18 PM - Customer: Please confirm the reference.\n"
            "12:20 PM - Agent: The case number is CASE-1234."
        )
        evidence = SimpleNamespace(
            id="evidence-chat",
            original_filename="bank-chat.pdf",
            extraction_status="extracted",
            extracted_text=source_text,
        )
        incident = SimpleNamespace(
            summary=None,
            incident_date=date(2026, 10, 12),
            incident_time=None,
        )

        events = generate_timeline_candidates([evidence], incident)
        self.assertEqual(len(events), 4)
        self.assertEqual([event["time"] for event in events], ["12:15", "12:17", "12:18", "12:20"])
        self.assertEqual({event["date"] for event in events}, {"2026-10-12"})
        self.assertTrue(all(event["source"] == "bank-chat.pdf" for event in events))
        self.assertEqual(events[0]["description"], "12:15 PM — Customer: I reported the unauthorized payment.")
        self.assertEqual(events[-1]["description"], "12:20 PM — Agent: The case number is CASE-1234.")
        self.assertTrue(all(event["review_status"] == "candidate" for event in events))

    def test_timeline_does_not_invent_date_when_transcript_or_incident_has_no_date(self):
        evidence = SimpleNamespace(
            id="evidence-chat",
            original_filename="undated-chat.pdf",
            extraction_status="extracted",
            extracted_text="12:15 PM - Sender: A message with no date.",
        )
        incident = SimpleNamespace(summary=None, incident_date=None, incident_time=None)
        event = generate_timeline_candidates([evidence], incident)[0]
        self.assertIsNone(event["date"])
        self.assertIsNone(event["date_text"])

    def test_missing_information_treats_whitespace_as_blank(self):
        incident = SimpleNamespace(
            incident_type="Online financial fraud",
            incident_date=None,
            incident_time=None,
            summary="   ",
            platform="  ",
            reported_identifier=None,
            location=" ",
            loss_amount=None,
            transaction_reference=None,
            workflow_data={"complainant": {"name": "  "}},
        )
        items = detect_missing_information(incident, [])
        status_by_field = {item["field"]: item["status"] for item in items}
        self.assertEqual(status_by_field["Incident description"], "needs_review")
        self.assertEqual(status_by_field["Platform or service"], "needs_review")
        self.assertEqual(status_by_field["Incident location"], "needs_review")
        self.assertEqual(status_by_field["Complainant details"], "needs_review")

    def test_local_guidance_retrieval_returns_sources_and_handles_empty_query(self):
        results = retrieve_guidance("preserve original evidence files")
        self.assertTrue(results)
        self.assertTrue(all(result["source"] for result in results))
        self.assertEqual(retrieve_guidance("and with the"), [])

    def test_workflow_apis_persist_information_timeline_draft_guidance(self):
        with self.Session() as session:
            incident = session.get(Incident, self.incident_id)
            incident.incident_type = "Online financial fraud"
            incident.summary = "A message requested a payment."
            evidence = session.get(Evidence, self.evidence_id)
            evidence.extraction_status = "extracted"
            evidence.extracted_text = (
                "On 2026-10-12 at 14:30, I paid INR 5,000. "
                "Transaction ID: ABCD123456."
            )
            session.commit()

            info = main.extract_evidence_information(self.evidence_id, session, self.client_id)
            self.assertIn("candidate", [fact["review_status"] for fact in info["structured_data"]["facts"]])
            timeline = main.generate_timeline(self.incident_id, session, self.client_id)
            self.assertEqual(timeline["events"][0]["date"], "2026-10-12")
            missing = main.get_missing_information(self.incident_id, session, self.client_id)
            self.assertTrue(any(item["status"] == "needs_review" for item in missing["items"]))
            draft = main.generate_complaint(self.incident_id, session, client_id=self.client_id)
            self.assertIn("DRAFT COMPLAINT", draft["text"])
            self.assertIn("Source |", draft["text"])
            self.assertIn("Unverified extracted candidate: 14:30 - I paid INR 5,000.", draft["text"])
            self.assertIn("ABCD123456", draft["text"])
            self.assertIn("scan.png", draft["text"])
            self.assertIn("not independently verified", draft["notice"])
            saved = main.update_complaint(
                self.incident_id,
                ComplaintUpdate(text=draft["text"] + "\nReviewed", reviewed=True),
                session,
                self.client_id,
            )
            self.assertTrue(saved["reviewed"])
            with patch(
                "main.retrieve_semantic_guidance",
                return_value=[{
                    "text": "Preserve the original evidence.",
                    "source_name": "Official synthetic guidance",
                    "publisher": "Synthetic agency",
                    "source_url": "https://example.gov.in/guidance",
                    "reference": "page 1",
                    "score": 0.9,
                }],
            ) as retrieve:
                guidance = main.retrieve_incident_guidance(
                    self.incident_id,
                    GuidanceSearch(query="preserve digital evidence"),
                    session,
                    self.client_id,
                )
            self.assertTrue(guidance["results"])
            retrieve.assert_called_once()
            self.assertEqual(retrieve.call_args.kwargs["incident_type"], "Online financial fraud")
            persisted = session.get(Incident, self.incident_id).workflow_data
            self.assertEqual(persisted["complaint"]["text"], saved["text"])
            self.assertEqual(persisted["guidance"]["query"], "preserve digital evidence")
            self.assertTrue(session.get(Evidence, self.evidence_id).structured_data["facts"])

    def test_ai_extraction_requires_consent_and_persists_structured_facts(self):
        fact_data = {
            "facts": [{
                "type": "amount",
                "value": "INR 5,000",
                "source_excerpt": "I paid INR 5,000.",
                "evidence_id": self.evidence_id,
                "evidence_filename": "scan.png",
                "review_status": "candidate",
            }],
            "method": "openai",
            "notice": "Synthetic AI extraction.",
        }
        with self.Session() as session:
            evidence = session.get(Evidence, self.evidence_id)
            evidence.extraction_status = "extracted"
            evidence.extracted_text = "I paid INR 5,000."
            session.commit()
            with patch("main.extract_evidence_facts", return_value=fact_data):
                result = main.extract_evidence_information_with_ai(
                    self.evidence_id,
                    ExternalAIConsent(consent_external_ai=True),
                    session,
                    self.client_id,
                )
            self.assertEqual(result["structured_data"]["method"], "openai")
            self.assertEqual(session.get(Evidence, self.evidence_id).structured_data["facts"][0]["value"], "INR 5,000")

    def test_user_fact_review_saves_confirmed_indices_for_ai_draft(self):
        with self.Session() as session:
            evidence = session.get(Evidence, self.evidence_id)
            evidence.structured_data = {
                "facts": [
                    {"type": "amount", "value": "INR 5,000", "source_excerpt": "I paid INR 5,000.", "review_status": "candidate"},
                    {"type": "transaction", "value": "TXN-123", "source_excerpt": "Reference TXN-123.", "review_status": "candidate"},
                ]
            }
            session.commit()
            result = main.review_evidence_facts(
                self.evidence_id,
                EvidenceFactsReview(confirmed_fact_indices=[0]),
                session,
                self.client_id,
            )
            self.assertEqual(result["structured_data"]["facts"][0]["review_status"], "confirmed")
            self.assertEqual(result["structured_data"]["facts"][1]["review_status"], "candidate")

    def test_complaint_uses_ai_with_consent_and_template_without_consent(self):
        ai_result = {
            "text": "AI draft cites synthetic evidence.",
            "method": "openai",
            "source_refs": ["evidence-test:0"],
            "notice": "Synthetic AI draft; review it.",
        }
        with self.Session() as session, patch(
            "main.generate_complaint_with_ai", return_value=ai_result
        ) as generate:
            result = main.generate_complaint(
                self.incident_id,
                session,
                ExternalAIConsent(consent_external_ai=True),
                self.client_id,
            )
            self.assertEqual(result["method"], "openai")
            self.assertIn(ai_result["text"], result["text"])
            self.assertIn("INCIDENT NARRATIVE", result["text"])
            self.assertIn("CHRONOLOGICAL TIMELINE", result["text"])
            self.assertIn("EVIDENCE PROVIDED", result["text"])
            fallback = main.generate_complaint(self.incident_id, session, client_id=self.client_id)
            self.assertEqual(fallback["method"], "local_template")
            self.assertIn("No case data was sent externally", fallback["notice"])
            self.assertEqual(session.get(Incident, self.incident_id).workflow_data["complaint"]["method"], "local_template")
            generate.assert_called_once()

    def test_complaint_falls_back_when_openai_is_not_configured(self):
        with self.Session() as session, patch(
            "main.generate_complaint_with_ai",
            side_effect=AIConfigurationError("OPENAI_API_KEY is not configured."),
        ):
            result = main.generate_complaint(
                self.incident_id,
                session,
                ExternalAIConsent(consent_external_ai=True),
                self.client_id,
            )
        self.assertEqual(result["method"], "local_template")
        self.assertIn("no case data was sent externally", result["notice"].lower())

    def test_complaint_failure_notice_does_not_claim_provider_received_no_data(self):
        with self.Session() as session, patch(
            "main.generate_complaint_with_ai",
            side_effect=AIProviderError("request failed"),
        ):
            result = main.generate_complaint(
                self.incident_id,
                session,
                ExternalAIConsent(consent_external_ai=True),
                self.client_id,
            )
        self.assertEqual(result["method"], "local_template")
        self.assertIn("may have received case data", result["notice"])

    def test_complaint_draft_uses_recorded_fields_confirmed_facts_and_source_tables(self):
        with self.Session() as session:
            incident = session.get(Incident, self.incident_id)
            incident.incident_type = "Online financial fraud"
            incident.incident_date = date(2026, 10, 12)
            incident.incident_time = time(14, 30)
            incident.platform = "Example Bank"
            incident.reported_identifier = "fraud@example.test"
            incident.transaction_reference = "TXN-654321"
            incident.loss_amount = "INR 5,000"
            incident.location = "Pune"
            incident.summary = "I received a message claiming to be from my bank and transferred INR 5,000."
            incident.requested_action = "Please review the transaction."
            incident.workflow_data = {
                "complainant": {"name": "Asha Example", "email": "asha@example.test"},
                "timeline": {
                    "events": [
                        {
                            "date": "2026-10-13",
                            "time": "10:00",
                            "description": "I contacted the bank.",
                            "source": "User entry",
                            "origin": "user",
                        },
                        {
                            "date": "2026-10-12",
                            "time": "14:30",
                            "description": "The transfer was made.",
                            "evidence_id": self.evidence_id,
                            "evidence_filename": "scan.png",
                            "origin": "evidence",
                            "review_status": "reviewed",
                        },
                    ]
                },
            }
            evidence = session.get(Evidence, self.evidence_id)
            evidence.category = "Financial or transaction"
            evidence.structured_data = {
                "facts": [
                    {
                        "type": "transaction_reference",
                        "value": "TXN-654321",
                        "source_excerpt": "Transaction ID TXN-654321.",
                        "review_status": "confirmed",
                    },
                    {
                        "type": "amount",
                        "value": "INR 99,999",
                        "source_excerpt": "Unreviewed candidate.",
                        "review_status": "candidate",
                    },
                ]
            }
            session.commit()

            draft = main.generate_complaint(self.incident_id, session, client_id=self.client_id)["text"]
            for value in (
                "Asha Example",
                "asha@example.test",
                "Online financial fraud",
                "2026-10-12",
                "14:30",
                "Example Bank",
                "fraud@example.test",
                "INR 5,000",
                "TXN-654321",
                "Pune",
                "Please review the transaction.",
                "I received a message claiming to be from my bank and transferred INR 5,000.",
            ):
                with self.subTest(value=value):
                    self.assertIn(value, draft)
            self.assertIn("| Date & Time | Event Description | Source |", draft)
            self.assertLess(draft.index("The transfer was made."), draft.index("I contacted the bank."))
            self.assertIn("Evidence ID: evidence-test", draft)
            self.assertIn("| scan.png | image/png | Financial or transaction; Evidence ID: evidence-test |", draft)
            self.assertIn("- Reported financial loss: ₹5,000", draft)
            self.assertIn("USER-CONFIRMED EVIDENCE FACTS", draft)
            self.assertNotIn("INR 99,999", draft)
            self.assertIn("MISSING-INFORMATION CHECKLIST", draft)
            self.assertIn("CLOSING", draft)
            self.assertIn("Complainant signature:", draft)
            self.assertNotIn("[Describe what happened", draft)
            self.assertNotIn("[Add your name", draft)
            self.assertNotIn("[No timeline events", draft)

    def test_complaint_draft_marks_absent_fields_as_not_provided(self):
        with self.Session() as session:
            incident = session.get(Incident, self.incident_id)
            incident.workflow_data = {}
            session.delete(session.get(Evidence, self.evidence_id))
            session.commit()
            draft = main.generate_complaint(self.incident_id, session, client_id=self.client_id)["text"]
            self.assertIn("Complainant's account: Not provided", draft)
            self.assertIn("No timeline events have been provided for this incident.", draft)
            self.assertIn("No evidence has been provided for this incident.", draft)
            self.assertIn("MISSING-INFORMATION CHECKLIST", draft)
            self.assertIn("Complainant details:", draft)
            self.assertIn("COMPLAINANT DETAILS\nNot provided", draft)
            self.assertNotIn("[", draft)
            self.assertIn("Complainant signature:", draft)
            main.update_complaint(
                self.incident_id,
                ComplaintUpdate(text=draft, reviewed=False),
                session,
                self.client_id,
            )
            response = main.export_complaint(self.incident_id, "pdf", session, self.client_id)
            pdf = fitz.open(stream=response.body, filetype="pdf")
            exported = "\n".join(page.get_text() for page in pdf)
            normalized_export = " ".join(exported.split()).replace("\u2010", "-")
            self.assertIn("Complainant's account: Not provided", normalized_export)
            self.assertIn("No timeline events have been provided for this incident.", normalized_export)
            self.assertIn("No evidence has been provided for this incident.", normalized_export)
            self.assertIn("COMPLAINANT DETAILS", normalized_export)
            pdf.close()

    def test_complaint_pdf_export_matches_saved_text_and_formats_tables_across_pages(self):
        with self.Session() as session:
            incident = session.get(Incident, self.incident_id)
            incident.incident_type = "Online financial fraud"
            incident.incident_date = date(2026, 10, 12)
            incident.summary = "I reported a transfer of INR 5,000."
            incident.workflow_data = {
                "timeline": {
                    "events": [
                        {
                            "date": f"2026-10-{day:02d}",
                            "time": "14:30",
                            "description": f"Recorded event {day}: " + "reviewed transfer details " * 24,
                            "evidence_id": self.evidence_id,
                            "evidence_filename": "scan.png",
                            "origin": "evidence",
                            "review_status": "reviewed",
                        }
                        for day in range(1, 21)
                    ]
                }
            }
            evidence = session.get(Evidence, self.evidence_id)
            evidence.category = "Financial or transaction"
            session.commit()

            generated = main.generate_complaint(self.incident_id, session, client_id=self.client_id)
            saved = main.update_complaint(
                self.incident_id,
                ComplaintUpdate(text=generated["text"], reviewed=False),
                session,
                self.client_id,
            )
            response = main.export_complaint(self.incident_id, "pdf", session, self.client_id)
            self.assertEqual(response.media_type, "application/pdf")
            pdf = fitz.open(stream=response.body, filetype="pdf")
            exported = "\n".join(page.get_text() for page in pdf)
            normalized_export = " ".join(exported.split()).replace("\u2010", "-")
            self.assertGreaterEqual(pdf.page_count, 2)
            self.assertIn("I reported a transfer of INR 5,000.", normalized_export)
            self.assertIn("CHRONOLOGICAL TIMELINE", normalized_export)
            self.assertIn("EVIDENCE PROVIDED", normalized_export)
            self.assertIn("scan.png", normalized_export)
            self.assertIn("Evidence ID: evidence-test", normalized_export)
            self.assertIn("MISSING-INFORMATION CHECKLIST", normalized_export)
            self.assertIn("Complainant signature:", normalized_export)
            self.assertIn("Page 1 of", normalized_export)
            for day in range(1, 21):
                self.assertIn(f"Recorded event {day}:", normalized_export)
            self.assertGreaterEqual(normalized_export.count("Date & Time"), 2)
            for page_number in range(1, pdf.page_count + 1):
                self.assertIn(f"Page {page_number} of {pdf.page_count}", normalized_export)
            self.assertEqual(
                saved["text"],
                session.get(Incident, self.incident_id).workflow_data["complaint"]["text"],
            )
            pdf.close()

    def test_timeline_update_rejects_unlinked_evidence(self):
        with self.Session() as session:
            with self.assertRaises(main.HTTPException) as error:
                main.update_timeline(
                    self.incident_id,
                    TimelineUpdate(
                        events=[
                            TimelineEventInput(
                                description="Unlinked",
                                evidence_id="another-incident-evidence",
                            )
                        ]
                    ),
                    session,
                    self.client_id,
                )
        self.assertEqual(error.exception.status_code, 422)

    def test_complaint_export_requires_a_saved_draft_and_supports_pdf(self):
        with self.Session() as session:
            with self.assertRaises(main.HTTPException) as error:
                main.export_complaint(self.incident_id, "pdf", session, self.client_id)
            self.assertEqual(error.exception.status_code, 409)
            main.update_complaint(
                self.incident_id,
                ComplaintUpdate(text="Synthetic complaint", reviewed=True),
                session,
                self.client_id,
            )
            pdf = main.export_complaint(self.incident_id, "pdf", session, self.client_id)
            self.assertEqual(pdf.media_type, "application/pdf")
            self.assertTrue(pdf.body.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
