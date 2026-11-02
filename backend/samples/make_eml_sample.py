"""Add one realistic .eml RFQ sample (rfq_16_eml) WITHOUT regenerating the others (keeps LLM caches valid).

The email has:
  - a free-text body with line items (some buyer shorthand)
  - a quoted earlier thread containing OLD items that must NOT be extracted
  - a PDF attachment with a line-item table
  - an unsupported attachment (.dwg) that should be skipped with a warning
Ground truth = body items + attachment items. Their exact SKUs are appended to data/catalog.csv.

Usage (from backend/):  python -m samples.make_eml_sample
"""
import csv
import json
import random
from email.message import EmailMessage
from pathlib import Path

from . import generate as g
from . import make_catalog as mc

ROOT = Path(__file__).parent
SID = "rfq_16_eml"


def main():
    r = random.Random(2026)
    body_items = [g.rand_item(r) for _ in range(4)]
    att_items = [g.rand_item(r) for _ in range(6)]
    old_items = [g.rand_item(r) for _ in range(3)]

    body_lines = [f"- {g.qty_txt(it)} {r.choice(g.UOM_TXT[it['uom']])} {g.describe(r, it, hard=i % 2 == 1)}"
                  for i, it in enumerate(body_items)]
    quoted = [f"> {g.qty_txt(it)} EA {g.describe(r, it)}" for it in old_items]
    text = "\n".join([
        "Hi team,", "",
        "Please quote the items below for the Baytown tie-in. Full line list attached as PDF.", "",
        *body_lines, "",
        "Need MTRs with shipment. Thanks,", "Priya", "Gulf Coast Fabricators", "",
        "On Tue, Oct 27, 2026 at 9:14 AM Sales <sales@distributor.example> wrote:",
        "> Thanks for the order last month:",
        *quoted,
    ])
    html = "<html><body>" + "".join(f"<p>{l}</p>" for l in text.split("\n")) + "</body></html>"

    pdf_path = ROOT / "files" / "_tmp_attachment.pdf"
    g.render_pdf(g.layout_table(r, att_items, 16), pdf_path)

    msg = EmailMessage()
    msg["From"] = "Priya Raman <priya@gcfab.example>"
    msg["To"] = "quotes@distributor.example"
    msg["Subject"] = "RFQ - Baytown tie-in (line list attached)"
    msg["Date"] = "Fri, 30 Oct 2026 10:02:00 -0500"
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename="baytown_line_list.pdf")
    msg.add_attachment(b"AC1027 fake dwg", maintype="application", subtype="acad", filename="tie-in_iso.dwg")
    pdf_path.unlink()

    out = ROOT / "files" / f"{SID}.eml"
    out.write_bytes(bytes(msg))
    items = body_items + att_items
    (ROOT / "truth" / f"{SID}.json").write_text(json.dumps({"id": SID, "items": items}, indent=1))

    index = json.loads((ROOT / "index.json").read_text())
    index = [s for s in index if s["id"] != SID] + [
        {"id": SID, "file": out.name, "layout": "eml", "title": "Email + PDF attachment", "n_items": len(items)}]
    (ROOT / "index.json").write_text(json.dumps(index, indent=1))

    cat = ROOT.parent / "data" / "catalog.csv"
    with cat.open() as fh:
        have = {row["sku"] for row in csv.DictReader(fh)}
    with cat.open("a", newline="") as fh:
        w = csv.writer(fh)
        added = 0
        for it in items:
            rec = {k: it[k] for k in mc.FIELDS}
            if mc.sku(rec) not in have:
                w.writerow([mc.sku(rec), mc.describe(rec), *[rec.get(f) or "" for f in mc.FIELDS], mc.price(rec), 40])
                have.add(mc.sku(rec))
                added += 1
    print(f"wrote {out} ({len(body_items)} body + {len(att_items)} attachment items, "
          f"{len(old_items)} quoted old items to ignore); +{added} catalog SKUs")


if __name__ == "__main__":
    main()
