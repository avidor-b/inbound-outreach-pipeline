"""Unit tests for types.py — typed artifacts and OperationContext."""
import unittest

from outreach_pipeline.types import (
    Contact, ContentPage, EmailDraft, ContentGrade, GradingReport,
    RAGResult, Reply, OperationContext,
)


class TestContact(unittest.TestCase):
    def test_from_db_row(self):
        row = {
            "id": "123",
            "properties": {"company": "Acme"},
            "company_context": "Makes rockets",
            "crm_context": "",
            "buying_intent": "high",
            "intent_signals": ["pricing", "timeline"],
            "score": 85,
            "score_reasoning": "Strong signals",
            "product_match": "RADIO-B100",
            "fetched_at": "2026-03-01T00:00:00Z",
            "enriched_at": "2026-03-01T01:00:00Z",
            "scored_at": "2026-03-01T02:00:00Z",
        }
        c = Contact.from_db_row(row)
        self.assertEqual(c.id, "123")
        self.assertEqual(c.properties["company"], "Acme")
        self.assertEqual(c.score, 85)
        self.assertEqual(c.intent_signals, ["pricing", "timeline"])

    def test_from_db_row_handles_none_fields(self):
        row = {"id": "1", "properties": {}}
        c = Contact.from_db_row(row)
        self.assertEqual(c.company_context, "")
        self.assertEqual(c.score, 0)
        self.assertIsNone(c.fetched_at)

    def test_to_db_dict(self):
        c = Contact(id="1", properties={"x": 1}, score=90, buying_intent="high")
        d = c.to_db_dict()
        self.assertEqual(d["id"], "1")
        self.assertEqual(d["score"], 90)
        self.assertNotIn("fetched_at", d)

    def test_roundtrip(self):
        original = Contact(
            id="42", properties={"msg": "hi"}, company_context="ctx",
            score=75, buying_intent="medium", intent_signals=["demo"],
        )
        row = {**original.to_db_dict(), "fetched_at": None, "enriched_at": None, "scored_at": None}
        rebuilt = Contact.from_db_row(row)
        self.assertEqual(original.id, rebuilt.id)
        self.assertEqual(original.score, rebuilt.score)
        self.assertEqual(original.intent_signals, rebuilt.intent_signals)


class TestContentPage(unittest.TestCase):
    def test_defaults(self):
        p = ContentPage(url="https://example.com", title="Home", text="Welcome")
        self.assertEqual(p.source_file, "")


class TestEmailDraft(unittest.TestCase):
    def test_defaults(self):
        d = EmailDraft(contact_id="1", subject="Hi", body="Hello")
        self.assertEqual(d.status, "draft")
        self.assertEqual(d.score_at_send, 0)


class TestContentGrade(unittest.TestCase):
    def test_defaults(self):
        page = ContentPage(url="u", title="t", text="txt")
        g = ContentGrade(page=page)
        self.assertEqual(g.quality_score, 100.0)
        self.assertEqual(g.violations, [])


class TestGradingReport(unittest.TestCase):
    def test_defaults(self):
        r = GradingReport()
        self.assertEqual(r.total_chunks, 0)
        self.assertEqual(r.overall_score, 100.0)


class TestRAGResult(unittest.TestCase):
    def test_fields(self):
        r = RAGResult(source="file.txt", text="chunk", distance=0.5)
        self.assertEqual(r.source, "file.txt")


class TestReply(unittest.TestCase):
    def test_defaults(self):
        r = Reply(contact_id="1")
        self.assertEqual(r.urgency, "")


class TestPydanticSerialization(unittest.TestCase):
    def test_contact_json_roundtrip(self):
        c = Contact(id="1", properties={"company": "Acme"}, score=80)
        j = c.model_dump_json()
        c2 = Contact.model_validate_json(j)
        self.assertEqual(c2.id, "1")
        self.assertEqual(c2.score, 80)

    def test_email_draft_schema(self):
        schema = EmailDraft.model_json_schema()
        self.assertIn("contact_id", schema["properties"])
        self.assertIn("subject", schema["properties"])

    def test_contact_validation_rejects_bad_type(self):
        with self.assertRaises(Exception):
            Contact(id=123)  # id must be str

    def test_rag_result_json_roundtrip(self):
        r = RAGResult(source="doc.txt", text="hello", distance=0.3, collection="test")
        j = r.model_dump_json()
        r2 = RAGResult.model_validate_json(j)
        self.assertEqual(r2.source, "doc.txt")
        self.assertEqual(r2.distance, 0.3)


class TestOperationContext(unittest.TestCase):
    def test_log(self):
        ctx = OperationContext(trace_id="abc")
        ctx.step_name = "fetch"
        ctx.log("started", count=5)
        self.assertEqual(len(ctx.audit_trail), 1)
        self.assertEqual(ctx.audit_trail[0]["event"], "started")
        self.assertEqual(ctx.audit_trail[0]["step"], "fetch")
        self.assertEqual(ctx.audit_trail[0]["count"], 5)

    def test_defaults(self):
        ctx = OperationContext()
        self.assertEqual(ctx.db_path, "data/pipeline.db")
        self.assertEqual(ctx.config, {})


if __name__ == "__main__":
    unittest.main()
