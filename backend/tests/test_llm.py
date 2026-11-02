"""LLM/OCR paths with the Gemini call mocked: tests the plumbing and the guardrails, not the model."""
from pathlib import Path

import pytest

from app import llmclient, llm, ocr, parse, pipeline, validate

SAMPLES = Path(__file__).parent.parent / "samples" / "files"


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def install(response):
        def fake(prompt, schema, **kw):
            calls.append({"prompt": prompt, **kw})
            return response(prompt, kw) if callable(response) else response
        monkeypatch.setattr(llmclient, "generate_json", fake)
        monkeypatch.setattr(llmclient, "available", lambda: True)
        monkeypatch.setenv("RFQ_LLM", "1")
        monkeypatch.setenv("RFQ_AGENT", "0")
        return calls
    return install


def _parsed(line):
    f, _ = parse.parse_line(line)
    validate.validate(f)
    return f


def test_fill_only_missing_and_keep_rule_values(fake_llm):
    fake_llm({"item_type": "elbow_90", "size": "3/4", "pressure_class": "3000", "material": "A105",
              "end_connection": "THD", "quantity": 50, "uom": "EA"})
    f = llm.fill_missing("* qty 50 ea: 3/4\" el 90 6k thd a105", _parsed("* qty 50 ea: 3/4\" el 90 6k thd a105"))
    assert f["item_type"].value == "elbow_90" and f["item_type"].source == "llm"
    assert f["pressure_class"].source == "llm"
    assert f["material"].source == "rule"  # rule value untouched


def test_off_vocabulary_values_are_rejected(fake_llm):
    fake_llm({"item_type": "sprocket", "material": "UNOBTAINIUM", "size": "7/9", "quantity": -3})
    f = llm.fill_missing("50 EA mystery part", _parsed("50 EA mystery part"))
    assert f["item_type"].value is None and f["material"].value is None and f["size"].value is None


def test_disagreement_is_flagged(fake_llm):
    fake_llm({"pressure_class": "300"})
    f = llm.fill_missing('4 EA 6" WN FLG 150# RF A105', _parsed('4 EA 6" WN FLG 150# RF A105'))
    assert f["pressure_class"].value == "150"
    assert any("disagree" in i for i in f["pressure_class"].issues)


def test_generic_material_resolved_by_llm(fake_llm):
    fake_llm({"material": "A403-WP316L"})
    f = llm.fill_missing('4 EA 4" X 2" CONC RED SCH 40 BW 316 SS', _parsed('4 EA 4" X 2" CONC RED SCH 40 BW 316 SS'))
    assert f["material"].value == "A403-WP316L" and f["material"].source == "llm"


def test_llm_fields_always_go_to_review(fake_llm):
    fake_llm({"item_type": "elbow_90"})
    items, _ = pipeline.process_lines([('8 EA 4" EL 90 SCH 40 BE A234-WPB', None)], use_llm=True)
    assert items[0].fields["item_type"].value == "elbow_90"
    assert items[0].status == "review"


def test_llm_only_mode(fake_llm):
    fake_llm([{"source_text": "x", "item_type": "tee", "size": "2", "schedule": "SCH 40", "material": "A234-WPB",
               "end_connection": "BW", "quantity": 4, "uom": "EA"}])
    res = pipeline.run((SAMPLES / "rfq_01_table.pdf").read_bytes(), "a.pdf", "llm-only")
    assert len(res.items) == 1 and res.items[0].fields["item_type"].source == "llm"


def test_rules_mode_makes_no_llm_calls(fake_llm):
    calls = fake_llm({})
    pipeline.run((SAMPLES / "rfq_07_email.pdf").read_bytes(), "a.pdf", "rules")
    assert calls == []


# ---------------------------------------------------------------- OCR

def test_agreement_scores():
    s = ocr.agreement(["1 4 EA 2\" TEE A105", "garbled line"], ["1 4 EA 2\" TEE A105", "totally different"])
    assert s[0] == 1.0 and s[1] < 0.6


def test_scan_pipeline_uses_ocr_confidence(fake_llm):
    lines = ["REQUEST FOR QUOTE", "1 4 EA 2\" 90 ELL SCH 40 BW A234 WPB", "2 6 EA 4\" TEE SCH 40 BW A234 WPB"]
    noisy = ["REQUEST FOR QUOTE", "1 4 EA 2\" 90 ELL SCH 40 BW A234 WPB", "2 6 EA 4\" TFE SCH 4O BW A23A WPB"]
    fake_llm(lambda prompt, kw: lines if kw.get("temperature", 0) == 0 else noisy)
    res = pipeline.run((SAMPLES / "rfq_13_scan.png").read_bytes(), "s.png", "rules+llm")
    assert [i.status for i in res.items] == ["auto", "review"]


def test_scan_pdf_renders_pages():
    imgs = ocr.page_images((SAMPLES / "rfq_15_scan.pdf").read_bytes(), "pdf")
    assert imgs and imgs[0][1] == "image/png" and imgs[0][0][:4] == b"\x89PNG"


def test_rules_mode_never_calls_llm_on_scans(fake_llm):
    calls = fake_llm(["x"])
    res = pipeline.run((SAMPLES / "rfq_13_scan.png").read_bytes(), "s.png", "rules")
    assert calls == [] and res.items == []
