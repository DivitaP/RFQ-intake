""".eml ingestion: body + attachments through one pipeline, quoted history ignored."""
from email.message import EmailMessage
from pathlib import Path

from app import email_ingest, extract, pipeline, segment

SAMPLE = Path(__file__).parent.parent / "samples" / "files" / "rfq_16_eml.eml"


def _eml(body: str, html: str | None = None, attachments=()) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "a@b.example", "q@d.example", "RFQ"
    if body:
        m.set_content(body)
    if html:
        if body:
            m.add_alternative(html, subtype="html")
        else:
            m.set_content(html, subtype="html")
    for name, data, (maintype, subtype) in attachments:
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return bytes(m)


def test_sniff_detects_eml():
    assert extract.sniff(SAMPLE.read_bytes(), "x.eml") == "email"
    assert extract.sniff(_eml("hi"), "noext") == "email"


def test_sample_email_end_to_end():
    r = pipeline.run(SAMPLE.read_bytes(), SAMPLE.name, "rules")
    assert r.kind == "email" and r.email["attachments"] == ["baytown_line_list.pdf"]
    origins = {it.origin for it in r.items}
    assert origins == {"email body", "attachment: baytown_line_list.pdf"}
    assert len(r.items) == 10
    assert any("tie-in_iso.dwg" in w for w in r.warnings)  # unsupported attachment reported, not silently dropped


def test_quoted_history_is_ignored():
    body = "Please quote:\n- 4 EA 6\" WN FLG 150# RF A105\n\nOn Tue, Oct 27, 2026 Sales wrote:\n> - 9 EA 2\" TEE SCH 40 BW WPB\n"
    r = pipeline.run(_eml(body), "x.eml", "rules")
    assert [it.fields["item_type"].value for it in r.items] == ["flange_wn"]


def test_html_only_body():
    html = "<html><body><p>Please quote</p><table><tr><td>4 EA</td><td>6\" WN FLG 150# RF A105</td></tr></table></body></html>"
    r = pipeline.run(_eml("", html=html), "x.eml", "rules")
    assert len(r.items) == 1 and r.items[0].fields["size"].value == "6"


def test_blank_line_and_prose_end_an_item():
    lines = ["- 12 EA 1\" UNION 3000# THD A105", "Need MTRs with shipment. Thanks,", "", "GR B"]
    assert segment.segment(lines) == ["- 12 EA 1\" UNION 3000# THD A105"]


def test_email_with_no_content():
    r = pipeline.run(_eml("", attachments=[("x.dwg", b"zz", ("application", "acad"))]), "x.eml", "rules")
    assert r.items == [] and r.warnings
