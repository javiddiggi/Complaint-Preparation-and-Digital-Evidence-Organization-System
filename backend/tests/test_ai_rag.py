import json
import os
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

import pymupdf
from chromadb.api.shared_system_client import SharedSystemClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_services import (
    build_complaint_context,
    extract_evidence_facts,
    generate_complaint_with_ai,
)
from rag import (
    _embedding_function,
    _vector_store,
    ingest_document,
    ingest_directory,
    knowledge_base_status,
    retrieve_guidance,
    validate_official_source_url,
)


class AIServiceTests(unittest.TestCase):
    def test_ai_fact_extraction_keeps_only_verifiable_excerpt_and_value(self):
        source_text = "Payment reference TXN-12345 was sent on 12 October."
        response = json.dumps(
            {
                "facts": [
                    {
                        "type": "transaction_reference",
                        "value": "TXN-12345",
                        "source_excerpt": "Payment reference TXN-12345 was sent on 12 October.",
                    },
                    {
                        "type": "amount",
                        "value": "INR 50,000",
                        "source_excerpt": "The evidence said INR 50,000.",
                    },
                ]
            }
        )
        with patch("ai_services._completion", return_value=response):
            result = extract_evidence_facts(source_text, "evidence-1", "receipt.txt")
        self.assertEqual(len(result["facts"]), 1)
        self.assertEqual(result["facts"][0]["value"], "TXN-12345")
        self.assertEqual(result["facts"][0]["review_status"], "candidate")
        self.assertEqual(result["facts"][0]["evidence_filename"], "receipt.txt")
        self.assertEqual(result["method"], "openai")

    def test_ai_complaint_context_excludes_unconfirmed_candidates_and_filters_sources(self):
        incident = type(
            "IncidentStub",
            (),
            {
                "incident_type": "Phishing",
                "incident_date": None,
                "incident_time": None,
                "platform": "Synthetic service",
                "reported_identifier": None,
                "transaction_reference": None,
                "loss_amount": None,
                "location": None,
                "summary": "A message requested a transfer.",
                "requested_action": None,
            },
        )()
        evidence = [
            type(
                "EvidenceStub",
                (),
                {
                    "id": "e1",
                    "original_filename": "synthetic.txt",
                    "category": "Other",
                    "extraction_status": "extracted",
                    "structured_data": {
                        "facts": [
                            {
                                "type": "amount",
                                "value": "INR 5,000",
                                "source_excerpt": "I paid INR 5,000.",
                                "review_status": "confirmed",
                            },
                            {
                                "type": "account",
                                "value": "999",
                                "source_excerpt": "Account number 999.",
                                "review_status": "candidate",
                            },
                        ]
                    },
                },
            )()
        ]
        context = build_complaint_context(
            incident,
            evidence,
            [
                {"description": "Unreviewed OCR event", "origin": "evidence", "review_status": "candidate"},
                {"description": "User-reported event", "origin": "user", "review_status": "reported"},
            ],
        )
        self.assertIn("INR 5,000", json.dumps(context))
        self.assertNotIn('"value": "999"', json.dumps(context))
        self.assertNotIn("Unreviewed OCR event", json.dumps(context))
        self.assertIn("User-reported event", json.dumps(context))
        response = json.dumps(
            {
                "draft_text": "The user reported paying INR 5,000 [Source: synthetic.txt].",
                "source_refs": [context["sources"][0]["id"], "invented-source"],
            }
        )
        with patch("ai_services._completion", return_value=response):
            result = generate_complaint_with_ai(context)
        self.assertEqual(result["source_refs"], [context["sources"][0]["id"]])
        self.assertIn("review every statement", result["notice"].lower())


class RagTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.document = self.root / "official-synthetic-guidance.txt"
        self.document.write_text(
            "For a suspected unauthorized bank transfer, promptly contact your bank using its official number. "
            "Do not disclose your one-time password, PIN, or online banking password to callers. "
            "Preserve transaction records and submit a report through the official cybercrime reporting portal.",
            encoding="utf-8",
        )
        self.chroma_directory = self.root / "chroma"
        self.env = patch.dict(os.environ, {"CYBER_ASSIST_CHROMA_PATH": str(self.chroma_directory)})
        self.env.start()
        _embedding_function.cache_clear()
        _vector_store.cache_clear()

    def tearDown(self):
        if _vector_store.cache_info().currsize:
            _vector_store()._client.close()
            _vector_store.cache_clear()
            SharedSystemClient.clear_system_cache()
        _vector_store.cache_clear()
        _embedding_function.cache_clear()
        self.env.stop()
        self.directory.cleanup()

    def test_ingest_and_semantically_retrieve_txt_with_citations(self):
        ingested = ingest_document(
            self.document,
            "Synthetic official guidance",
            "Synthetic government agency",
            "https://example.gov.in/cyber-guidance",
            jurisdiction="India",
        )
        self.assertGreater(ingested["chunks_ingested"], 0)
        results = retrieve_guidance(
            "In India, I lost money after a fraudulent bank payment. How do I protect my account?",
            incident_type="Online financial fraud",
            extracted_information=["payment transfer"],
        )
        self.assertTrue(results)
        self.assertIn("official number", results[0]["text"])
        self.assertEqual(results[0]["source_name"], "Synthetic official guidance")
        self.assertEqual(results[0]["publisher"], "Synthetic government agency")
        self.assertEqual(results[0]["source_url"], "https://example.gov.in/cyber-guidance")
        self.assertEqual(results[0]["reference"].split(",")[0], "text document")
        self.assertIsNone(results[0]["page_number"])
        self.assertEqual(results[0]["source_type"], "official_document")
        self.assertEqual(results[0]["jurisdiction"], "India")

    def test_official_source_url_validation_rejects_nonofficial_hosts(self):
        with self.assertRaisesRegex(ValueError, "approved official"):
            validate_official_source_url("https://example.com/guidance")
        with self.assertRaisesRegex(ValueError, "approved official"):
            validate_official_source_url("https://cert-in.org.in.attacker.example/guidance")

    def test_ingest_pdf_and_status_preserve_page_source_metadata(self):
        pdf_path = self.root / "official-synthetic.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Official synthetic PDF guidance says preserve original transaction records.")
        document.save(pdf_path)
        document.close()
        result = ingest_document(
            pdf_path,
            "Synthetic official PDF",
            "Synthetic government agency",
            "https://example.gov.in/pdf-guidance",
            "2025-03-01",
            "official_document",
            "India",
        )
        self.assertEqual(result["chunks_ingested"], 1)
        status = knowledge_base_status()
        self.assertTrue(status["ready"])
        self.assertIn("Synthetic official PDF", status["documents"])
        passages = retrieve_guidance("preserve transaction records")
        self.assertEqual(passages[0]["document_name"], pdf_path.name)
        self.assertIn("page 1", passages[0]["reference"])
        self.assertEqual(passages[0]["page_number"], 1)
        self.assertEqual(passages[0]["publication_date"], "2025-03-01")
        self.assertEqual(passages[0]["jurisdiction"], "India")

    def test_indian_guidance_is_default_and_us_reference_requires_explicit_scope(self):
        india_document = self.root / "india-guidance.txt"
        india_document.write_text(
            "Indian reporting guidance: preserve transaction records and contact the official Indian portal.",
            encoding="utf-8",
        )
        us_document = self.root / "us-summary.txt"
        us_document.write_text(
            "United States IC3 summaries describe US-specific complaint procedures and evidence.",
            encoding="utf-8",
        )
        ingest_document(
            india_document,
            "Indian reporting source",
            "Synthetic Indian government authority",
            "https://example.gov.in/reporting",
            source_type="official_document",
            jurisdiction="India",
        )
        ingest_document(
            us_document,
            "IC3 US-specific summary",
            "FBI Internet Crime Complaint Center (IC3)",
            "https://www.ic3.gov/Home/FAQ",
            source_type="application_summary",
            jurisdiction="United States",
        )
        results = retrieve_guidance("How do I report a cybercrime complaint?", limit=10)
        self.assertTrue(results)
        self.assertTrue(all(result["jurisdiction"] == "India" for result in results))
        us_results = retrieve_guidance("How does a victim file a complaint with IC3?", limit=10)
        self.assertTrue(us_results)
        self.assertTrue(all(result["jurisdiction"] == "United States" for result in us_results))

    def test_verified_indian_sources_are_ingested_cited_and_retrievable(self):
        project_root = Path(__file__).resolve().parents[2]
        knowledge_base = project_root / "backend" / "knowledge_base"
        manifest = knowledge_base / "sources.example.json"
        ingested = ingest_directory(knowledge_base, manifest)
        newly_indexed = {
            "cert-in-directions-70b-page.txt",
            "cert-in-directions-70b-2022.pdf",
            "cert-in-directions-70b-faq-2022.pdf",
            "cert-in-directions-extension-2022.pdf",
            "i4c-nctau-malicious-sideloaded-apps-advisory-2026.pdf",
            "i4c-national-cybercrime-reporting-portal.txt",
            "i4c-cybercrime-faq.txt",
            "i4c-cybercrime-categories.txt",
        }
        with manifest.open(encoding="utf-8") as source_file:
            manifest_documents = json.load(source_file)["documents"]
        added_sources = {
            metadata["source_name"]
            for path, metadata in manifest_documents.items()
            if Path(path).name in newly_indexed
        }
        by_source = {item["source_name"]: item for item in ingested}
        self.assertEqual(len(added_sources), len(newly_indexed))
        self.assertTrue(added_sources.issubset(by_source))
        for name in added_sources:
            source = by_source[name]
            self.assertGreater(source["chunks_ingested"], 0)
            self.assertEqual(source["jurisdiction"], "India")
            self.assertIn(source["source_type"], {"official_document", "official_web_page"})
            self.assertTrue(source["publisher"])
            self.assertTrue(source["source_url"].startswith("https://"))

        questions = [
            ("How do I report online financial fraud in India?", "1930"),
            ("What information is required when filing a cybercrime complaint in India?", "phone number"),
            ("What evidence should be preserved when filing a cybercrime complaint in India?", "potential evidence"),
            ("How can cybercrime against women and children be reported in India?", "Report Crime related to Women"),
            ("What do CERT-In's cyber incident reporting directions under Section 70B cover in India?", "reporting of cyber incidents"),
        ]
        for question, expected_text in questions:
            with self.subTest(question=question):
                passages = retrieve_guidance(question, limit=10)
                self.assertTrue(passages, question)
                self.assertTrue(
                    any(expected_text.lower() in item["text"].lower() for item in passages),
                    f"No relevant Indian passage for {question!r}: "
                    f"{[(item['source_title'], item['text'][:180]) for item in passages]}",
                )
                for item in passages:
                    self.assertEqual(item["jurisdiction"], "India")
                    self.assertIn(item["source_type"], {"official_document", "official_web_page"})
                    self.assertTrue(item["source_title"])
                    self.assertTrue(item["issuing_authority"])
                    self.assertTrue(item["source_url"].startswith("https://"))
                    self.assertTrue(item["reference"])

        directions = retrieve_guidance(
            "CERT-In India cyber incident reporting directions under Section 70B dated 28 April 2022",
            limit=10,
        )
        self.assertTrue(any(item["source_url"].endswith("CERT-In_Directions_70B_28.04.2022.pdf") for item in directions))
        self.assertTrue(
            any(item["page_number"] is not None for item in directions if item["source_url"].endswith(".pdf"))
        )

    def test_empty_knowledge_base_returns_no_passages(self):
        self.assertEqual(retrieve_guidance("report online fraud"), [])


if __name__ == "__main__":
    unittest.main()
