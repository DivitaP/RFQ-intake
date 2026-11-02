"""Generate synthetic PVF RFQs + ground truth.

Each RFQ is built from canonical line items, then rendered with realistic alias variation
(2" / 2 IN / NPS 2 / DN50, 150# / CL150, A234 WPB / WPB, ...). Ground truth = the canonical items,
so labels are exact and free.

Layouts:
  table    clean tabular RFQ (text PDF)
  email    free-text "please quote" style with mixed case + a noise note line (text PDF)
  wrapped  table where long descriptions wrap onto a second line (text PDF)  <- base segmenter fails
  scan     table rendered to a degraded image (PNG / image-only PDF)       <- base has no OCR

Usage:  python -m samples.generate        (from backend/)
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageFilter, ImageFont, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent
FILES, TRUTH = ROOT / "files", ROOT / "truth"
SEED = 7

NPS_TO_DN = {"1/2": "15", "3/4": "20", "1": "25", "1-1/2": "40", "2": "50", "3": "80", "4": "100",
             "6": "150", "8": "200", "10": "250", "12": "300"}
DECIMAL = {"1/2": "0.5", "3/4": "0.75", "1-1/2": "1.5"}

CUSTOMERS = ["Gulf Coast Fabricators", "Permian Process Services", "Bayou Mechanical",
             "Lone Star Piping Co.", "Brazos Valley Industrial", "Ship Channel Constructors"]

# ---------------------------------------------------------------- canonical items

def rand_item(r: random.Random) -> dict:
    kind = r.choices(["pipe", "bw", "forged", "flange", "valve"], [2, 4, 3, 3, 2])[0]
    it = {"item_type": None, "size": None, "schedule": None, "pressure_class": None,
          "material": None, "end_connection": None, "quantity": None, "uom": "EA"}
    if kind == "pipe":
        it.update(item_type="pipe", size=r.choice(["2", "3", "4", "6", "8", "10"]),
                  schedule=r.choice(["SCH 40", "SCH 80", "STD"]),
                  material=r.choice(["A106-B", "A106-B", "A53-B", "A312-TP316L"]),
                  end_connection=r.choice(["BW", "PE"]), quantity=float(r.choice([21, 42, 63, 120, 240])), uom="FT")
    elif kind == "bw":
        t = r.choice(["elbow_90", "elbow_90", "elbow_45", "tee", "reducer", "cap"])
        size = r.choice(["2", "3", "4", "6", "8"])
        if t == "reducer":
            big = r.choice(["3", "4", "6", "8"])
            small = {"3": "2", "4": "2", "6": "4", "8": "6"}[big]
            size = f"{big}x{small}"
        it.update(item_type=t, size=size, schedule=r.choice(["SCH 40", "SCH 80", "STD"]),
                  material=r.choice(["A234-WPB", "A234-WPB", "A403-WP316L"]), end_connection="BW",
                  quantity=float(r.choice([2, 4, 6, 8, 10, 12, 20])))
    elif kind == "forged":
        it.update(item_type=r.choice(["elbow_90", "tee", "coupling", "union", "cap"]),
                  size=r.choice(["1/2", "3/4", "1", "1-1/2", "2"]),
                  pressure_class=r.choice(["3000", "3000", "6000"]),
                  material=r.choice(["A105", "A105", "A182-F316L"]),
                  end_connection=r.choice(["SW", "THD"]), quantity=float(r.choice([5, 10, 12, 25, 50])))
    elif kind == "flange":
        it.update(item_type=r.choice(["flange_wn", "flange_so", "flange_blind"]),
                  size=r.choice(["2", "3", "4", "6", "8", "10", "12"]),
                  pressure_class=r.choice(["150", "150", "300", "600"]),
                  material=r.choice(["A105", "A105", "A182-F316L", "A350-LF2"]),
                  end_connection=r.choice(["RF", "RF", "RF", "RTJ"]), quantity=float(r.choice([2, 4, 6, 8, 16])))
    else:
        t = r.choice(["valve_gate", "valve_ball", "valve_check", "valve_globe"])
        if r.random() < 0.3:
            it.update(item_type=t, size=r.choice(["1/2", "3/4", "1", "2"]), pressure_class="800",
                      material="A105", end_connection=r.choice(["SW", "THD"]))
        else:
            it.update(item_type=t, size=r.choice(["2", "3", "4", "6", "8"]),
                      pressure_class=r.choice(["150", "300", "600"]),
                      material=r.choice(["A216-WCB", "A216-WCB", "A351-CF8M"]), end_connection="RF")
        it["quantity"] = float(r.choice([1, 2, 3, 4, 6]))
    return it


# ---------------------------------------------------------------- alias rendering

def t_size(r, s):
    if "x" in s:
        a, b = s.split("x")
        return f"{t_size1(r, a, allow_dn=False)} X {t_size1(r, b, allow_dn=False)}"
    return t_size1(r, s)


def t_size1(r, s, allow_dn=True):
    opts = [f'{s}"', f'{s}"', f"{s} IN", f"NPS {s}"]
    if s in DECIMAL:
        opts.append(f'{DECIMAL[s]}"')
    if "-" in s:
        opts.append(f'{s.replace("-", " ")}"')
    if allow_dn and s in NPS_TO_DN:
        opts.append(f"DN{NPS_TO_DN[s]}")
    return r.choice(opts)


TYPE_TXT = {
    "elbow_90": ["90 ELL", "90 LR ELL", "90 DEG ELBOW", "90L"],
    "elbow_45": ["45 ELL", "45 DEG ELBOW", "45L"],
    "tee": ["TEE", "STRAIGHT TEE"],
    "reducer": ["CONC RED", "CONCENTRIC REDUCER", "ECC RED"],
    "cap": ["CAP"],
    "coupling": ["COUPLING", "CPLG"],
    "union": ["UNION"],
    "pipe": ["SMLS PIPE", "PIPE SMLS", "PIPE"],
    "flange_wn": ["WN FLG", "WELD NECK FLANGE"],
    "flange_so": ["SO FLG", "SLIP-ON FLANGE"],
    "flange_blind": ["BLIND FLANGE", "BLIND FLG"],
    "valve_gate": ["GATE VALVE", "GATE VLV"],
    "valve_ball": ["BALL VALVE", "BALL VLV"],
    "valve_check": ["SWING CHECK VALVE", "CHECK VALVE"],
    "valve_globe": ["GLOBE VALVE"],
}
MAT_TXT = {
    "A106-B": ["A106 GR B", "A106-B", "ASTM A106 B"], "A53-B": ["A53 GR B", "A53-B"],
    "A312-TP316L": ["A312 TP316L", "TP316L"], "A234-WPB": ["A234 WPB", "A234-WPB", "WPB"],
    "A403-WP316L": ["A403 WP316L", "WP316L"], "A105": ["A105", "A105N", "ASTM A105"],
    "A182-F316L": ["A182 F316L", "F316L"], "A350-LF2": ["A350 LF2", "LF2"],
    "A216-WCB": ["A216 WCB", "WCB"], "A351-CF8M": ["A351 CF8M", "CF8M"],
}
END_TXT = {"BW": ["BW", "BUTT WELD", "BE"], "PE": ["PE", "PLAIN END"], "SW": ["SW", "SOCKET WELD"],
           "THD": ["THD", "NPT", "THREADED"], "RF": ["RF"], "RTJ": ["RTJ"]}
UOM_TXT = {"EA": ["EA", "EA", "PCS", "EACH"], "FT": ["FT", "FT", "LF"]}


def t_class(r, c):
    return r.choice([f"{c}#", f"CL{c}", f"CLASS {c}", f"{c} LB"])


def t_sched(r, s):
    if s.startswith("SCH"):
        n = s.split()[1]
        return r.choice([s, f"SCH{n}", f"S/{n}"])
    return s


# "Hard" shorthand real buyers use that the base rules don't cover (LLM / extended-rules territory).
HARD_SIZE = {"1/2": "half inch", "3/4": "three quarter inch", "1": "one inch", "2": "two inch",
             "3": "three inch", "4": "four inch", "6": "six inch", "8": "eight inch"}
HARD_TYPE = {"flange_so": "S/O", "flange_wn": "W/N", "elbow_90": "EL 90", "valve_gate": "GT VLV",
             "valve_ball": "BV", "tee": "T"}
HARD_CLASS = {"3000": "3K", "6000": "6K", "150": "#150", "300": "#300", "600": "#600"}
HARD_MAT = {"A234-WPB": "WP-B", "A106-B": "106B", "A105": "CS FORGED", "A216-WCB": "CS BODY"}


def describe(r: random.Random, it: dict, hard: bool = False) -> str:
    """hard=True swaps 1-2 attributes for shorthand the base rules don't know."""
    tricks = set(r.sample(["size", "type", "class", "mat"], r.randint(1, 2))) if hard else set()
    size = HARD_SIZE.get(it["size"]) if "size" in tricks else None
    typ = HARD_TYPE.get(it["item_type"]) if "type" in tricks else None
    parts = [size or t_size(r, it["size"]), typ or r.choice(TYPE_TXT[it["item_type"]])]
    if it["pressure_class"]:
        hc = HARD_CLASS.get(it["pressure_class"]) if "class" in tricks else None
        parts.append(hc or t_class(r, it["pressure_class"]))
    if it["schedule"]:
        parts.append(t_sched(r, it["schedule"]))
    end = r.choice(END_TXT[it["end_connection"]])
    if it["item_type"].startswith("valve") and it["end_connection"] == "RF":
        end = "RF FLGD"
    parts.append(end)
    hm = HARD_MAT.get(it["material"]) if "mat" in tricks else None
    parts.append(hm or r.choice(MAT_TXT[it["material"]]))
    return " ".join(parts)


def qty_txt(it):
    q = it["quantity"]
    return str(int(q)) if q == int(q) else str(q)


# ---------------------------------------------------------------- layouts

def header(r, n):
    return [r.choice(CUSTOMERS), f"REQUEST FOR QUOTE  RFQ-{2600 + n}", "Delivery: Houston, TX  |  Need by: 2 weeks", ""]


def layout_table(r, items, n):
    lines = header(r, n) + ["ITEM  QTY  UOM  DESCRIPTION"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}  {qty_txt(it)}  {r.choice(UOM_TXT[it['uom']])}  {describe(r, it)}")
    return lines


def layout_wrapped(r, items, n):
    lines = header(r, n) + ["ITEM  QTY  UOM  DESCRIPTION"]
    for i, it in enumerate(items, 1):
        desc = describe(r, it)
        row = f"{i}  {qty_txt(it)}  {r.choice(UOM_TXT[it['uom']])}  "
        words = desc.split()
        if r.random() < 0.6 and len(words) > 4:
            cut = len(words) - 2
            lines.append(row + " ".join(words[:cut]))
            lines.append("            " + " ".join(words[cut:]))
        else:
            lines.append(row + desc)
    return lines


def layout_email(r, items, n):
    lines = [f"From: buyer@{r.choice(['gcfab', 'permianps', 'bayoumech'])}.com",
             f"Subject: RFQ - {r.choice(['unit 3 turnaround', 'tie-in package', 'skid job'])}", "",
             "Hi team, please quote the following:", ""]
    for it in items:
        d = describe(r, it, hard=r.random() < 0.5)
        q, u = qty_txt(it), r.choice(UOM_TXT[it["uom"]])
        s = r.choice([f"- {q} {u} {d}", f"{d} - {q} {u}", f"* qty {q} {u}: {d}"])
        lines.append(s.lower() if r.random() < 0.3 else s)
    lines += ["", "Note: all flanges to match pipe schedule, MTRs required.", "Thanks,", "Mike"]
    return lines


# ---------------------------------------------------------------- renderers

def render_pdf(lines, path):
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for ln in lines:
        if y < 60:
            c.showPage()
            y = 740
        c.setFont("Helvetica-Bold" if ln.startswith(("REQUEST", "ITEM")) else "Helvetica", 10)
        c.drawString(50, y, ln)
        y -= 16
    c.save()


def _font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/Library/Fonts/Courier New.ttf",
              "C:/Windows/Fonts/cour.ttf"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def render_scan(r, lines, path):
    W, H = 1700, 2200
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    f = _font(30)
    y = 120
    for ln in lines:
        d.text((110, y), ln, fill=r.randint(10, 60), font=f)
        y += 46
    img = img.rotate(r.uniform(-1.8, 1.8), fillcolor=245, resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(r.uniform(0.8, 1.4)))
    px = img.load()
    for _ in range(W * H // 60):
        x, yy = r.randrange(W), r.randrange(H)
        px[x, yy] = r.randint(120, 255)
    img = img.resize((W // 2, H // 2))
    if path.suffix == ".pdf":
        img.convert("RGB").save(path, "PDF", resolution=100)
    else:
        img.save(path)


# ---------------------------------------------------------------- main

PLAN = (
    [("table", "pdf")] * 5 + [("email", "pdf")] * 4 + [("wrapped", "pdf")] * 3
    + [("scan", "png")] * 2 + [("scan", "pdf")]
)
TITLES = {"table": "Clean tabular RFQ", "email": "Free-text email RFQ", "wrapped": "Table with wrapped descriptions",
          "scan": "Scanned RFQ"}


def main():
    r = random.Random(SEED)
    FILES.mkdir(exist_ok=True)
    TRUTH.mkdir(exist_ok=True)
    index = []
    for n, (layout, ext) in enumerate(PLAN, 1):
        items = [rand_item(r) for _ in range(r.randint(6, 12))]
        build = {"table": layout_table, "email": layout_email, "wrapped": layout_wrapped,
                 "scan": layout_table}[layout]
        lines = build(r, items, n)
        sid = f"rfq_{n:02d}_{layout}"
        path = FILES / f"{sid}.{ext}"
        if layout == "scan":
            render_scan(r, lines, path)
        else:
            render_pdf(lines, path)
        (TRUTH / f"{sid}.json").write_text(json.dumps({"id": sid, "items": items}, indent=1))
        index.append({"id": sid, "file": path.name, "layout": layout, "title": TITLES[layout],
                      "n_items": len(items)})
    (ROOT / "index.json").write_text(json.dumps(index, indent=1))
    print(f"wrote {len(index)} RFQs to {FILES}")


if __name__ == "__main__":
    main()
