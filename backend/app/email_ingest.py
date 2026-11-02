"""Email (.eml) ingestion: an RFQ often arrives as a body ("please quote the following...") plus
attachments (a PDF line list, a phone photo of a markup). Each becomes a Part that flows through
the same pipeline; every line item keeps its origin ("email body" / "attachment: x.pdf").

Body handling:
  - prefers text/plain, falls back to HTML -> text
  - drops quoted history ("> ..." lines and everything after "On ... wrote:" / "Original Message"),
    so a reply thread doesn't re-quote last month's RFQ as new line items
"""
import email
import re
from dataclasses import dataclass, field
from email import policy
from html.parser import HTMLParser

MAX_ATTACHMENTS = 5
MAX_BODY_CHARS = 20000
ATTACHMENT_TYPES = {"application/pdf": "pdf", "image/png": "image", "image/jpeg": "image", "image/jpg": "image"}
EXT_TYPES = {".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image"}

QUOTE_CUT = re.compile(r"^(On .{0,200} wrote:|-{2,}\s*Original Message\s*-{2,}|From: .+ Sent: )", re.I)


@dataclass
class Part:
    name: str               # "email body" | "attachment: file.pdf"
    ftype: str              # "text" | "pdf" | "image"
    data: bytes = b""
    text: str = ""


@dataclass
class ParsedEmail:
    subject: str = ""
    sender: str = ""
    date: str = ""
    body: str = ""
    parts: list[Part] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def looks_like_eml(data: bytes, filename: str) -> bool:
    if filename.lower().endswith(".eml"):
        return True
    head = data[:2048].decode("utf-8", errors="ignore")
    return bool(re.search(r"^(From|Received|Return-Path|MIME-Version|Message-ID):", head, re.M)) and \
        bool(re.search(r"^(Subject|Content-Type):", head, re.M | re.I))


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append("  ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n", "".join(p.out)).strip()


def strip_quoted(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if QUOTE_CUT.match(line.strip()):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse(data: bytes) -> ParsedEmail:
    msg = email.message_from_bytes(data, policy=policy.default)
    out = ParsedEmail(subject=str(msg.get("subject", "")), sender=str(msg.get("from", "")),
                      date=str(msg.get("date", "")))

    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is not None:
        content = body_part.get_content()
        text = html_to_text(content) if body_part.get_content_type() == "text/html" else content
        out.body = strip_quoted(text)[:MAX_BODY_CHARS]
    if out.body:
        out.parts.append(Part(name="email body", ftype="text", text=out.body))

    n = 0
    for att in msg.iter_attachments():
        name = att.get_filename() or "attachment"
        ctype = att.get_content_type()
        ftype = ATTACHMENT_TYPES.get(ctype) or next((v for k, v in EXT_TYPES.items() if name.lower().endswith(k)), None)
        if not ftype:
            out.skipped.append(f"{name} ({ctype}): unsupported attachment type")
            continue
        if n >= MAX_ATTACHMENTS:
            out.skipped.append(f"{name}: more than {MAX_ATTACHMENTS} attachments")
            continue
        payload = att.get_payload(decode=True) or b""
        out.parts.append(Part(name=f"attachment: {name}", ftype=ftype, data=payload))
        n += 1
    return out
