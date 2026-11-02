"""Domain vocabulary for industrial PVF (pipe, valves, fittings).

Every alias maps to ONE canonical value. Order matters: more specific patterns first.
This file is the main place domain knowledge lives — extend it, don't scatter regexes.
"""
import re

# ---------------------------------------------------------------------------
# Item types. Category drives which fields are required and which rules apply.
#   pipe   -> size, schedule, material, qty (FT/JT)
#   bw     -> butt-weld fittings: size, schedule, material, qty
#   forged -> SW/THD fittings: size, class (2000/3000/6000), material, end, qty
#   flange -> size, class (150..2500), material, face, qty
#   valve  -> size, class, material, end, qty
# ---------------------------------------------------------------------------
ITEM_TYPES: list[tuple[str, str]] = [
    ("valve_gate", r"\bGATE\s*(VALVE|VLV)\b|\bGV\b"),
    ("valve_ball", r"\bBALL\s*(VALVE|VLV)\b"),
    ("valve_check", r"\b(SWING\s*)?(CHECK|CHK)\s*(VALVE|VLV)\b"),
    ("valve_globe", r"\bGLOBE\s*(VALVE|VLV)?\b"),
    ("flange_blind", r"\bBLIND\b|\bBLRF\b|\bBLD\s*FLG\b"),
    ("flange_wn", r"\bWELD\s*-?\s*NECK\b|\bWNRF\b|\bWN\s*(FLG|FLANGE)\b"),
    ("flange_so", r"\bSLIP\s*-?\s*ON\b|\bSORF\b|\bSO\s*(FLG|FLANGE)\b"),
    ("flange_sw", r"\b(SOCKET\s*WELD|SW)\s*(FLG|FLANGE)\b|\bSWRF\b"),
    ("elbow_45", r"\b45\s*(DEG\.?|°)?\s*(LR\s*|SR\s*)?(ELL|ELBOW|EL)\b|\b45L\b"),
    ("elbow_90", r"\b90\s*(DEG\.?|°)?\s*(LR\s*|SR\s*)?(ELL|ELBOW|EL)\b|\b90L\b|\bELL\b|\bELBOW\b"),
    ("tee", r"\bTEE\b"),
    ("reducer", r"\b(CONC|ECC|CONCENTRIC|ECCENTRIC)\.?\s*(RED|REDUCER)\b|\bREDUCER\b|\bSWAGE\b"),
    ("cap", r"\bCAP\b"),
    ("coupling", r"\bCOUPLING\b|\bCPLG\b"),
    ("union", r"\bUNION\b"),
    ("pipe", r"\bPIPE\b|\bSMLS\b|\bERW\b"),
]

FITTING_TYPES = {"elbow_90", "elbow_45", "tee", "reducer", "cap", "coupling", "union"}
FLANGE_TYPES = {"flange_blind", "flange_wn", "flange_so", "flange_sw"}
VALVE_TYPES = {"valve_gate", "valve_ball", "valve_check", "valve_globe"}

# ---------------------------------------------------------------------------
# End connections / flange faces
# ---------------------------------------------------------------------------
END_CONNECTIONS: list[tuple[str, str]] = [
    ("RTJ", r"\bRTJ\b|\bRING\s*TYPE\s*JOINT\b"),
    ("RF", r"\bRF\b|\bRAISED\s*FACE\b|WNRF|SORF|BLRF|SWRF|\bRF\s*FLGD\b"),
    ("FF", r"\bFF\b|\bFLAT\s*FACE\b"),
    ("BW", r"\bBW\b|\bBUTT\s*-?\s*WELD\b|\bBEVEL(LED|ED)?\s*END\b|\bBE\b"),
    ("SW", r"\bSW\b|\bSOCKET\s*-?\s*WELD\b|\bSKT\s*WLD\b"),
    ("THD", r"\bTHD\b|\bTHRD\b|\bTHREADED\b|\bF?NPT\b|\bSCRD\b|\bSCREWED\b"),
    ("PE", r"\bPE\b|\bPLAIN\s*END\b"),
]

# ---------------------------------------------------------------------------
# Schedules / pressure classes
# ---------------------------------------------------------------------------
SCHEDULE_RE = re.compile(r"\b(?:SCH(?:EDULE)?\.?\s*|S/)(\d{2,3}S?|STD|XS|XXS)\b|\b(STD|XXS|XS)\b(?!\s*\d)")
CLASS_RE = re.compile(r"\b(\d{3,4})\s*(?:#|LBS?\b)|\bCL(?:ASS)?\.?\s*(\d{3,4})\b")

FLANGE_VALVE_CLASSES = {"150", "300", "600", "800", "900", "1500", "2500"}
FORGED_CLASSES = {"2000", "3000", "6000", "9000"}

# ---------------------------------------------------------------------------
# Sizes (NPS, inches). Canonical form: "1/2", "3/4", "1", "1-1/2", "2", ...
# ---------------------------------------------------------------------------
VALID_NPS = [
    "1/8", "1/4", "3/8", "1/2", "3/4", "1", "1-1/4", "1-1/2", "2", "2-1/2", "3", "3-1/2",
    "4", "5", "6", "8", "10", "12", "14", "16", "18", "20", "24", "30", "36",
]
DN_TO_NPS = {
    "15": "1/2", "20": "3/4", "25": "1", "32": "1-1/4", "40": "1-1/2", "50": "2", "65": "2-1/2",
    "80": "3", "100": "4", "125": "5", "150": "6", "200": "8", "250": "10", "300": "12",
    "350": "14", "400": "16", "450": "18", "500": "20", "600": "24",
}
DECIMAL_TO_NPS = {"0.125": "1/8", "0.25": "1/4", "0.375": "3/8", "0.5": "1/2", "0.75": "3/4",
                  "1.25": "1-1/4", "1.5": "1-1/2", "2.5": "2-1/2", "3.5": "3-1/2"}

_NUM = r"\d+[-\s]\d/\d|\d/\d|\d*\.\d+|\d+"  # longest forms first: 1-1/2, 3/4, 0.75, 2
SIZE_RE = re.compile(
    rf"(?:\bNPS\s*({_NUM}))"                          # NPS 2
    rf"|(?:\bDN\s*(\d{{2,3}}))"                        # DN50
    rf"|(?:({_NUM})\s*(?:\"|''|\bIN\b|\bINCH(?:ES)?\b|IN\b))"  # 2" / 2 IN / 2in
)

# ---------------------------------------------------------------------------
# Materials. forms = which product forms the spec legitimately covers.
# generic=True means "family only, ASTM spec not stated" -> low confidence.
# ---------------------------------------------------------------------------
MATERIALS: list[dict] = [
    {"canon": "A182-F316L", "re": r"\bA-?\s*182\s*(GR\.?\s*)?F?316L?\b|\bF316L?\b", "forms": {"forged"}},
    {"canon": "A312-TP316L", "re": r"\bA-?\s*312\s*(TP)?316L?\b|\bTP316L?\b", "forms": {"pipe"}},
    {"canon": "A403-WP316L", "re": r"\bA-?\s*403\s*(WP)?316L?\b|\bWP316L?\b", "forms": {"bw"}},
    {"canon": "A351-CF8M", "re": r"\bA-?\s*351\s*(CF8M)?\b|\bCF8M\b", "forms": {"cast"}},
    {"canon": "A350-LF2", "re": r"\bA-?\s*350\s*(LF2)?\b|\bLF2\b", "forms": {"forged"}},
    {"canon": "A333-6", "re": r"\bA-?\s*333\s*(GR\.?\s*)?6?\b", "forms": {"pipe"}},
    {"canon": "A216-WCB", "re": r"\bA-?\s*216\s*(WCB)?\b|\bWCB\b", "forms": {"cast"}},
    {"canon": "A234-WPB", "re": r"\bA-?\s*234\s*(WPB)?\b|\bWPB\b", "forms": {"bw"}},
    {"canon": "A106-B", "re": r"\bA-?\s*106\s*(GR\.?\s*|GRADE\s*)?B?\b", "forms": {"pipe"}},
    {"canon": "A53-B", "re": r"\bA-?\s*53\s*(GR\.?\s*|GRADE\s*)?B?\b", "forms": {"pipe"}},
    {"canon": "A105", "re": r"\bA-?\s*105N?\b", "forms": {"forged"}},
    # generic families last
    {"canon": "SS316", "re": r"\bSS\s*316L?\b|\b316L?\s*SS\b|\b316L?\s*STAINLESS\b", "forms": None, "generic": True},
    {"canon": "CS", "re": r"\bCARBON\s*STEEL\b|\bC\.S\.?(?=\s|$)|\bCS\b", "forms": None, "generic": True},
]

UOM_ALIASES = {
    "EA": "EA", "EACH": "EA", "PC": "EA", "PCS": "EA", "PIECE": "EA", "PIECES": "EA",
    "FT": "FT", "FEET": "FT", "LF": "FT", "FOOT": "FT",
    "JT": "JT", "JTS": "JT", "JOINT": "JT", "JOINTS": "JT", "LENGTH": "JT", "LENGTHS": "JT",
}
_UOM_ALT = "|".join(sorted(UOM_ALIASES, key=len, reverse=True))
# qty followed by a UOM, e.g. "10 EA", "120 FT", "1,200 LF"
QTY_UOM_RE = re.compile(rf"(?<![\d/.\-])(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s*({_UOM_ALT})\b")
# table row: "<line#> <qty> <uom> description"
ROW_PREFIX_RE = re.compile(rf"^\s*(\d{{1,3}})[.)]?\s+(\d{{1,3}}(?:,\d{{3}})+|\d+(?:\.\d+)?)\s+({_UOM_ALT})\b")
# "QTY: 10" / "QTY 10"
QTY_LABEL_RE = re.compile(r"\bQTY\.?\s*[:=]?\s*(\d+(?:\.\d+)?)")
