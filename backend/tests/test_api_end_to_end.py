from io import BytesIO
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pymupdf
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from database import Base, get_db
from ocr import OcrResult


class ApiEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

        def override_db():
            with self.Session() as session:
                yield session

        self.original_uploads = main.UPLOADS_DIR
        main.UPLOADS_DIR = self.root / "uploads"
        main.app.dependency_overrides[get_db] = override_db
        self.client = TestClient(main.app, headers={"X-Client-ID": "demo-user-one"})

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.pop(get_db, None)
        main.UPLOADS_DIR = self.original_uploads
        self.engine.dispose()
        self.directory.cleanup()

    def test_two_incident_complaints_keep_details_timeline_evidence_and_exports_separate(self):
        first = self.client.post(
            "/incidents",
            json={
                "incident_type": "Online financial fraud",
                "incident_date": "2026-10-12",
                "incident_time": "14:30",
                "platform": "Example Bank",
                "transaction_reference": "FIRST-TXN-431",
                "loss_amount": "INR 5,000",
                "location": "Pune",
                "summary": "First incident narrative for synchronization testing.",
            },
        ).json()["incident"]
        second = self.client.post(
            "/incidents",
            json={
                "incident_type": "Account access issue",
                "incident_date": "2026-11-03",
                "platform": "Example Social Platform",
                "summary": "Second incident narrative for synchronization testing.",
            },
        ).json()["incident"]
        first_id, second_id = first["id"], second["id"]

        evidence = self.client.post(
            f"/incidents/{first_id}/evidence",
            files={"file": ("first-source.txt", b"First incident source reference.", "text/plain")},
            data={"category": "Incident document"},
        )
        self.assertEqual(evidence.status_code, 201, evidence.text)
        first_evidence_id = evidence.json()["id"]
        first_timeline = self.client.put(
            f"/incidents/{first_id}/timeline",
            json={"events": [
                {"date": "2026-10-12", "time": "14:30", "description": "First incident event.", "origin": "user"},
                {"date": "2026-10-13", "time": "09:00", "description": "First incident follow-up.", "origin": "user"},
            ]},
        )
        second_timeline = self.client.put(
            f"/incidents/{second_id}/timeline",
            json={"events": [
                {"date": "2026-11-03", "time": "08:15", "description": "Second incident event.", "origin": "user"},
            ]},
        )
        self.assertEqual(first_timeline.status_code, 200, first_timeline.text)
        self.assertEqual(second_timeline.status_code, 200, second_timeline.text)
        first_saved_timeline = self.client.get(f"/incidents/{first_id}/timeline").json()["events"]
        second_saved_timeline = self.client.get(f"/incidents/{second_id}/timeline").json()["events"]
        self.assertEqual(
            [event["description"] for event in first_saved_timeline],
            ["First incident event.", "First incident follow-up."],
        )
        self.assertEqual(
            [event["description"] for event in second_saved_timeline],
            ["Second incident event."],
        )
        first_missing = self.client.get(f"/incidents/{first_id}/missing-information").json()["items"]
        second_missing = self.client.get(f"/incidents/{second_id}/missing-information").json()["items"]
        first_evidence_status = next(item["status"] for item in first_missing if item["field"] == "Supporting evidence")
        second_evidence_status = next(item["status"] for item in second_missing if item["field"] == "Supporting evidence")
        self.assertEqual(first_evidence_status, "recorded")
        self.assertEqual(second_evidence_status, "needs_review")

        first_draft = self.client.post(f"/incidents/{first_id}/generate-complaint").json()["text"]
        second_draft = self.client.post(f"/incidents/{second_id}/generate-complaint").json()["text"]
        self.assertIn("First incident narrative for synchronization testing.", first_draft)
        self.assertIn("FIRST-TXN-431", first_draft)
        self.assertIn("First incident event.", first_draft)
        self.assertIn("First incident follow-up.", first_draft)
        self.assertIn(first_evidence_id, first_draft)
        self.assertNotIn("Second incident narrative", first_draft)
        self.assertNotIn("Second incident event.", first_draft)
        self.assertIn("Second incident narrative for synchronization testing.", second_draft)
        self.assertIn("Second incident event.", second_draft)
        self.assertNotIn("First incident narrative", second_draft)
        self.assertNotIn("First incident event.", second_draft)
        self.assertNotIn(first_evidence_id, second_draft)

        edited_first = first_draft + "\nReviewed first-incident amendment."
        saved_first = self.client.put(
            f"/incidents/{first_id}/complaint",
            json={"text": edited_first, "reviewed": True},
        )
        self.assertEqual(saved_first.status_code, 200, saved_first.text)
        self.assertEqual(self.client.get(f"/incidents/{second_id}/complaint").json()["text"], second_draft)
        self.assertEqual(self.client.get(f"/incidents/{first_id}/complaint").json()["text"], edited_first)

        pdf_response = self.client.get(f"/incidents/{first_id}/complaint/export?format=pdf")
        self.assertEqual(pdf_response.status_code, 200)
        pdf = pymupdf.open(stream=pdf_response.content, filetype="pdf")
        exported_text = "\n".join(page.get_text() for page in pdf)
        normalized_pdf_text = " ".join(exported_text.split()).replace("\u2010", "-")
        self.assertIn("Reviewed first-incident amendment.", normalized_pdf_text)
        self.assertIn("First incident narrative for synchronization testing.", normalized_pdf_text)
        self.assertNotIn("Second incident narrative", exported_text)
        pdf.close()

    def test_complaint_pdf_labels_ocr_candidates_and_only_lists_missing_fields(self):
        incident = self.client.post(
            "/incidents",
            json={
                "incident_type": "Online financial fraud",
                "incident_date": "2026-11-05",
                "incident_time": "11:05",
                "platform": "WhatsApp",
                "transaction_reference": "TEST12345",
                "loss_amount": "2500",
                "location": "Pune",
                "summary": "The complainant reports losing ₹2,500 after a refund message.",
            },
        ).json()["incident"]
        incident_id = incident["id"]
        image_buffer = BytesIO()
        Image.new("RGB", (20, 20), "white").save(image_buffer, format="PNG")
        evidence = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("WhatsApp Refund Scam Evidence.png", image_buffer.getvalue(), "image/png")},
            data={"category": "Communication"},
        )
        self.assertEqual(evidence.status_code, 201, evidence.text)
        evidence_id = evidence.json()["id"]

        blank_event = self.client.put(
            f"/incidents/{incident_id}/timeline",
            json={"events": [{
                "date": "2026-11-06",
                "description": "   ",
                "source": "Empty user event",
                "origin": "user",
            }]},
        )
        self.assertEqual(blank_event.status_code, 200, blank_event.text)
        with patch(
            "main.extract_evidence_ocr",
            return_value=OcrResult(
                "ocr_extracted",
                "11:13 AM — Done, | have entered my UPI PIN. Oho so &",
                "OCR succeeded.",
            ),
        ):
            ocr_result = self.client.post(f"/evidence/{evidence_id}/ocr")
        self.assertEqual(ocr_result.status_code, 200, ocr_result.text)
        generated_timeline = self.client.post(f"/incidents/{incident_id}/generate-timeline")
        self.assertEqual(generated_timeline.status_code, 200, generated_timeline.text)

        draft = self.client.post(f"/incidents/{incident_id}/generate-complaint")
        self.assertEqual(draft.status_code, 200, draft.text)
        text = draft.json()["text"]
        self.assertIn("₹2,500", text)
        self.assertIn("TEST12345", text)
        self.assertIn("| Date & Time | Event Description | Source |", text)
        self.assertIn("Incident Form", text)
        self.assertNotIn("Empty user event", text)
        self.assertIn("WhatsApp Refund Scam Evidence.png", text)
        self.assertIn("OCR extracted candidate; verify against the original", text)
        self.assertIn(r"Done, \| have entered my UPI PIN.", text)
        self.assertIn("Oho so &", text)
        self.assertIn("| Evidence File | Evidence Type | Description / Reference |", text)
        self.assertIn("image/png", text)
        self.assertNotIn("Incident location", text)
        self.assertIn("Complainant details", text)
        self.assertNotIn("Pune: Not provided", text)
        self.assertNotIn("INCORRECT-TXN", text)

        response = self.client.get(f"/incidents/{incident_id}/complaint/export?format=pdf")
        self.assertEqual(response.status_code, 200)
        pdf = pymupdf.open(stream=response.content, filetype="pdf")
        exported_text = "\n".join(page.get_text() for page in pdf)
        normalized_pdf_text = " ".join(exported_text.split())
        self.assertIn("₹2,500", normalized_pdf_text)
        self.assertIn("TEST12345", normalized_pdf_text)
        self.assertIn("WhatsApp Refund Scam Evidence.png", normalized_pdf_text)
        self.assertIn("Date & Time", normalized_pdf_text)
        self.assertIn("Evidence Type", normalized_pdf_text)
        self.assertIn("Not provided", normalized_pdf_text)
        self.assertIn("OCR extracted candidate", normalized_pdf_text)
        self.assertIn("Oho so &", normalized_pdf_text)
        self.assertNotIn("INCORRECT-TXN", normalized_pdf_text)
        self.assertIn(f"Page 1 of {pdf.page_count}", normalized_pdf_text)
        pdf.close()

    def test_demo_workspace_scope_isolates_incidents_evidence_and_complaints(self):
        user_one = {"X-Client-ID": "demo-user-one", "X-Client-Case-ID": "case-one"}
        user_two = {"X-Client-ID": "demo-user-two", "X-Client-Case-ID": "case-two"}
        first = self.client.post(
            "/incidents",
            headers=user_one,
            json={"incident_type": "User one incident", "summary": "Private first draft facts."},
        ).json()["incident"]
        second = self.client.post(
            "/incidents",
            headers=user_two,
            json={"incident_type": "User two incident", "summary": "Private second draft facts."},
        ).json()["incident"]
        first_id, second_id = first["id"], second["id"]
        evidence = self.client.post(
            f"/incidents/{first_id}/evidence",
            headers=user_one,
            files={"file": ("user-one.txt", b"Private first evidence.", "text/plain")},
            data={"category": "Communication"},
        )
        self.assertEqual(evidence.status_code, 201, evidence.text)
        evidence_id = evidence.json()["id"]
        self.assertEqual(
            [incident["id"] for incident in self.client.get("/incidents", headers=user_one).json()],
            [first_id],
        )
        self.assertEqual(
            [incident["id"] for incident in self.client.get("/incidents", headers=user_two).json()],
            [second_id],
        )
        unscoped_client = TestClient(main.app)
        self.assertEqual(unscoped_client.get("/incidents").status_code, 403)
        unscoped_client.close()
        self.assertEqual(self.client.get(f"/incidents/{first_id}", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/incidents/{first_id}/timeline", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/incidents/{first_id}/complaint", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/incidents/{first_id}/complaint/export?format=pdf", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/evidence/{evidence_id}", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/evidence/{evidence_id}/download", headers=user_two).status_code, 404)
        self.assertEqual(
            self.client.delete(f"/evidence/{evidence_id}", headers=user_two).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/incidents/{first_id}", headers=user_two).status_code, 404)
        self.assertEqual(self.client.get(f"/incidents/{second_id}/evidence", headers=user_two).json(), [])
        for owner, incident_id, expected, excluded in (
            (user_one, first_id, "Private first draft facts.", "Private second draft facts."),
            (user_two, second_id, "Private second draft facts.", "Private first draft facts."),
        ):
            generated = self.client.post(
                f"/incidents/{incident_id}/generate-complaint",
                headers=owner,
            )
            self.assertEqual(generated.status_code, 200, generated.text)
            self.assertIn(expected, generated.json()["text"])
            self.assertNotIn(excluded, generated.json()["text"])
            pdf_response = self.client.get(
                f"/incidents/{incident_id}/complaint/export?format=pdf",
                headers=owner,
            )
            self.assertEqual(pdf_response.status_code, 200)
            pdf = pymupdf.open(stream=pdf_response.content, filetype="pdf")
            pdf_text = "\n".join(page.get_text() for page in pdf)
            normalized_pdf_text = " ".join(pdf_text.split()).replace("\u2010", "-")
            self.assertIn(expected, normalized_pdf_text)
            self.assertNotIn(excluded, pdf_text)
            pdf.close()

    def test_pdf_extraction_generates_idempotent_timestamp_timeline(self):
        incident = self.client.post(
            "/incidents",
            json={
                "incident_type": "Online financial fraud",
                "incident_date": "2026-10-12",
                "summary": "The user reported the incident.",
            },
        ).json()["incident"]
        incident_id = incident["id"]
        transcript = (
            "Chat transcript\n"
            "12:15 PM - Customer: I reported the unauthorized payment.\n"
            "12:17 PM - Agent: We have blocked the card.\n"
            "12:18 PM - Customer: Please confirm the reference.\n"
            "12:20 PM - Agent: The case number is CASE-1234."
        )
        document = pymupdf.open()
        page = document.new_page()
        page.insert_textbox((72, 72, 520, 720), transcript, fontsize=12)
        pdf_content = document.tobytes()
        document.close()
        uploaded = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("bank-chat.pdf", pdf_content, "application/pdf")},
            data={"category": "Communication"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        evidence_id = uploaded.json()["id"]
        extracted = self.client.post(f"/evidence/{evidence_id}/extract-text")
        self.assertEqual(extracted.status_code, 200, extracted.text)
        for marker in ("12:15 PM", "12:17 PM", "12:18 PM", "12:20 PM"):
            self.assertIn(marker, extracted.json()["extracted_text"])

        manual_event = {
            "date": "2026-10-12",
            "description": "Manual event retained across generation.",
            "source": "User entry",
            "origin": "user",
            "review_status": "reviewed",
        }
        saved_manual = self.client.put(
            f"/incidents/{incident_id}/timeline",
            json={"events": [manual_event]},
        )
        self.assertEqual(saved_manual.status_code, 200, saved_manual.text)

        first_generation = self.client.post(f"/incidents/{incident_id}/generate-timeline")
        second_generation = self.client.post(f"/incidents/{incident_id}/generate-timeline")
        self.assertEqual(first_generation.status_code, 200, first_generation.text)
        self.assertEqual(second_generation.status_code, 200, second_generation.text)
        events = self.client.get(f"/incidents/{incident_id}/timeline").json()["events"]
        self.assertEqual(len(events), 6)
        self.assertEqual(events[0]["description"], "Manual event retained across generation.")
        candidates = [event for event in events if event["origin"] == "evidence"]
        self.assertEqual([event["time"] for event in candidates], ["12:15", "12:17", "12:18", "12:20"])
        self.assertTrue(all(event["date"] == "2026-10-12" for event in candidates))
        self.assertTrue(all(event["source"] == "bank-chat.pdf" for event in candidates))
        self.assertTrue(all(event["evidence_filename"] == "bank-chat.pdf" for event in candidates))

    def test_api_routes_cover_persisted_evidence_to_complaint_workflow(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/health").json(), {"status": "healthy"})
        with patch("main.knowledge_base_status", return_value={"ready": True, "chunks": 2, "documents": ["test"]}):
            self.assertEqual(self.client.get("/knowledge-base/status").status_code, 200)

        incident_response = self.client.post(
            "/incidents",
            json={
                "incident_type": "Online financial fraud",
                "incident_date": "2026-10-12",
                "platform": "Synthetic payment service",
                "summary": "A synthetic message requested a payment.",
            },
        )
        self.assertEqual(incident_response.status_code, 201)
        incident_id = incident_response.json()["incident"]["id"]
        self.assertEqual(self.client.get("/incidents").status_code, 200)
        self.assertEqual(self.client.get(f"/incidents/{incident_id}").status_code, 200)
        updated = self.client.put(
            f"/incidents/{incident_id}",
            json={"requested_action": "Review the synthetic transaction."},
        )
        self.assertEqual(updated.status_code, 200)

        evidence_response = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("transaction.txt", b"On 2026-10-12 at 14:30, I paid INR 5,000. Transaction ID: TEST-12345.", "text/plain")},
            data={"category": "Financial or transaction"},
        )
        self.assertEqual(evidence_response.status_code, 201, evidence_response.text)
        evidence_id = evidence_response.json()["id"]
        self.assertEqual(self.client.get(f"/incidents/{incident_id}/evidence").status_code, 200)
        self.assertEqual(self.client.get(f"/evidence/{evidence_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/evidence/{evidence_id}/download").content[:5], b"On 20")
        self.assertEqual(
            self.client.post(f"/evidence/{evidence_id}/extract-text").json()["status"],
            "extracted",
        )
        self.assertIn("TEST-12345", self.client.get(f"/evidence/{evidence_id}/text").json()["extracted_text"])
        pdf_document = pymupdf.open()
        pdf_document.new_page().insert_text((72, 72), "Synthetic PDF extraction marker PDF-TEST-9876.")
        pdf_content = pdf_document.tobytes()
        pdf_document.close()
        pdf_response = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("receipt.pdf", pdf_content, "application/pdf")},
            data={"category": "Incident document"},
        )
        self.assertEqual(pdf_response.status_code, 201, pdf_response.text)
        pdf_evidence_id = pdf_response.json()["id"]
        pdf_text = self.client.post(f"/evidence/{pdf_evidence_id}/extract-text")
        self.assertEqual(pdf_text.status_code, 200)
        self.assertIn("PDF-TEST-9876", pdf_text.json()["extracted_text"])
        image_buffer = BytesIO()
        Image.new("RGB", (200, 80), "white").save(image_buffer, format="PNG")
        image_response = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("sample.png", image_buffer.getvalue(), "image/png")},
            data={"category": "Incident document"},
        )
        self.assertEqual(image_response.status_code, 201, image_response.text)
        image_evidence_id = image_response.json()["id"]
        with patch(
            "main.extract_evidence_ocr",
            return_value=OcrResult("ocr_extracted", "Synthetic OCR API result.", "OCR succeeded."),
        ):
            ocr_response = self.client.post(f"/evidence/{image_evidence_id}/ocr")
        self.assertEqual(ocr_response.status_code, 200)
        self.assertEqual(ocr_response.json()["status"], "ocr_extracted")
        self.assertEqual(
            self.client.get(f"/evidence/{image_evidence_id}/text").json()["extracted_text"],
            "Synthetic OCR API result.",
        )
        facts = self.client.post(f"/evidence/{evidence_id}/extract-information")
        self.assertEqual(facts.status_code, 200)
        self.assertTrue(facts.json()["structured_data"]["facts"])
        with patch(
            "main.extract_evidence_facts",
            return_value={
                "facts": [{
                    "type": "transaction_reference",
                    "value": "TEST-12345",
                    "source_excerpt": "Transaction ID: TEST-12345.",
                    "evidence_id": evidence_id,
                    "evidence_filename": "transaction.txt",
                    "review_status": "candidate",
                }],
                "method": "openai",
                "notice": "Synthetic.",
            },
        ) as ai:
            no_consent = self.client.post(f"/evidence/{evidence_id}/extract-information/ai", json={})
            self.assertEqual(no_consent.status_code, 422)
            self.assertFalse(ai.called)
            consented = self.client.post(
                f"/evidence/{evidence_id}/extract-information/ai",
                json={"consent_external_ai": True},
            )
            self.assertEqual(consented.status_code, 200)
            ai.assert_called_once()
        reviewed = self.client.patch(
            f"/evidence/{evidence_id}/structured-information",
            json={"confirmed_fact_indices": [0]},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)

        timeline = self.client.post(f"/incidents/{incident_id}/generate-timeline")
        self.assertEqual(timeline.status_code, 200)
        self.assertEqual(self.client.get(f"/incidents/{incident_id}/timeline").status_code, 200)
        events = timeline.json()["events"]
        events.append({"description": "User reviewed synthetic case details.", "origin": "user"})
        self.assertEqual(
            self.client.put(f"/incidents/{incident_id}/timeline", json={"events": events}).status_code,
            200,
        )
        self.assertEqual(self.client.get(f"/incidents/{incident_id}/missing-information").status_code, 200)

        draft = self.client.post(f"/incidents/{incident_id}/generate-complaint")
        self.assertEqual(draft.status_code, 200)
        self.assertIn("DRAFT COMPLAINT", draft.json()["text"])
        self.assertEqual(self.client.get(f"/incidents/{incident_id}/complaint").status_code, 200)
        saved = self.client.put(
            f"/incidents/{incident_id}/complaint",
            json={"text": draft.json()["text"] + "\nReviewed.", "reviewed": True},
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            self.client.get(f"/incidents/{incident_id}/complaint/export?format=txt").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f"/incidents/{incident_id}/complaint/export?format=pdf").headers["content-type"],
            "application/pdf",
        )

        source = {
            "text": "Preserve the original records.",
            "source_name": "Synthetic official source",
            "source_title": "Synthetic official source",
            "publisher": "Synthetic authority",
            "source_url": "https://example.gov.in/guidance",
            "source_type": "official_document",
            "jurisdiction": "India",
            "publication_date": None,
            "page_number": 1,
            "reference": "page 1",
            "score": 0.9,
        }
        with patch("main.retrieve_semantic_guidance", return_value=[source]):
            self.assertEqual(self.client.get("/guidance/search", params={"query": "preserve records"}).status_code, 200)
            guidance = self.client.post(
                f"/incidents/{incident_id}/guidance",
                json={"query": "preserve records"},
            )
        self.assertEqual(guidance.status_code, 200)
        self.assertEqual(guidance.json()["results"][0]["source_url"], source["source_url"])
        self.assertEqual(
            self.client.get(f"/incidents/{incident_id}").json()["workflow_data"]["guidance"]["query"],
            "preserve records",
        )

        self.assertEqual(self.client.delete(f"/evidence/{evidence_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/evidence/{pdf_evidence_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/evidence/{image_evidence_id}").status_code, 200)
        self.assertEqual(self.client.delete(f"/incidents/{incident_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/incidents/{incident_id}").status_code, 404)

    def test_upload_rejects_empty_and_mismatched_content(self):
        response = self.client.post(
            "/incidents",
            json={"incident_type": "Other"},
        )
        incident_id = response.json()["incident"]["id"]
        empty = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"category": "Other"},
        )
        self.assertEqual(empty.status_code, 400)
        mismatch = self.client.post(
            f"/incidents/{incident_id}/evidence",
            files={"file": ("not-a-pdf.pdf", b"not a PDF", "application/pdf")},
            data={"category": "Other"},
        )
        self.assertEqual(mismatch.status_code, 400)

    def test_knowledge_ingestion_endpoint_requires_admin_key_and_preserves_metadata(self):
        file_data = b"An official synthetic source describes cyber incident reporting."
        long_title = "Verified official source title " + ("extension " * 24)
        with patch.dict(os.environ, {"KNOWLEDGE_BASE_ADMIN_KEY": "test-key"}), patch(
            "main.ingest_document",
            return_value={
                "source_name": "Synthetic source",
                "publisher": "Synthetic authority",
                "source_url": "https://example.gov.in/source",
                "publication_date": "2025-01-02",
                "source_type": "official_document",
                "jurisdiction": "India",
                "chunks_ingested": 1,
            },
        ) as ingest:
            unauthorized = self.client.post(
                "/knowledge-base/ingest",
                files={"file": ("source.txt", file_data, "text/plain")},
                data={"source_name": "Synthetic source", "publisher": "Synthetic authority", "source_url": "https://example.gov.in/source"},
            )
            self.assertEqual(unauthorized.status_code, 403)
            authorized = self.client.post(
                "/knowledge-base/ingest",
                headers={"X-KB-Admin-Key": "test-key"},
                files={"file": ("source.txt", file_data, "text/plain")},
                data={
                    "source_name": long_title,
                    "publisher": "Synthetic authority",
                    "source_url": "https://example.gov.in/source",
                    "publication_date": "2025-01-02",
                    "source_type": "official_document",
                    "jurisdiction": "India",
                },
            )
            self.assertEqual(authorized.status_code, 200, authorized.text)
            self.assertEqual(ingest.call_args.args[1], long_title)
            self.assertEqual(ingest.call_args.args[4:], ("2025-01-02", "official_document", "India"))


if __name__ == "__main__":
    unittest.main()
