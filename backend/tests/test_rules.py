import pytest

from app import confidence, parse, validate


def run(line):
    f, _ = parse.parse_line(line)
    issues = validate.validate(f)
    confidence.score(f)
    return f, confidence.status(f, issues)


@pytest.mark.parametrize("text,size", [
    ('2"', "2"), ("2 IN", "2"), ("NPS 3/4", "3/4"), ("DN50", "2"), ('1 1/2"', "1-1/2"),
    ('1.5"', "1-1/2"), ('4" X 2"', "4x2"),
])
def test_size(text, size):
    assert parse.parse_size(parse.clean(text)) == size


@pytest.mark.parametrize("text,cls", [("150#", "150"), ("CL300", "300"), ("CLASS 600", "600"), ("3000 LB", "3000")])
def test_class(text, cls):
    assert parse.parse_class(parse.clean(text)) == cls


def test_clean_bw_elbow_auto_approves():
    f, status = run('1 10 EA 2" 90 LR ELL SCH 40 BW A234 WPB')
    assert status == "auto"
    assert f["item_type"].value == "elbow_90" and f["material"].value == "A234-WPB"


def test_forged_material_on_bw_fitting_goes_to_review():
    f, status = run('8 EA 2" 90 ELL SCH 40 BW A105')
    assert status == "review"
    assert f["material"].issues


def test_generic_material_goes_to_review():
    _, status = run('4 EA 4" X 2" CONC RED SCH 40 BW 316 SS')
    assert status == "review"


def test_missing_type_goes_to_review():
    _, status = run('50 EA 3/4" EL 90 6K THD A105')
    assert status == "review"


# ---------------------------------------------------------------- segmentation
from app import segment  # noqa: E402


def test_wrapped_description_is_merged():
    lines = ["ITEM QTY UOM DESCRIPTION", "1 8 EACH NPS 6 BLIND FLANGE CLASS 150", "RF A105N",
             "2 2 EA 4\" BALL VLV CL150 RF FLGD", "A216 WCB"]
    assert segment.segment(lines) == ["1 8 EACH NPS 6 BLIND FLANGE CLASS 150 RF A105N",
                                      "2 2 EA 4\" BALL VLV CL150 RF FLGD A216 WCB"]


def test_note_and_signature_are_not_items_or_continuations():
    lines = ["- 4 EA 6\" WN FLG 150# RF A105", "Note: all flanges to match pipe schedule, MTRs required.",
             "Thanks,", "Mike"]
    assert segment.segment(lines) == ["- 4 EA 6\" WN FLG 150# RF A105"]


def test_unknown_type_with_qty_still_becomes_review_item():
    out = segment.segment(["* qty 50 ea: three quarter inch el 90 6k thd a105"])
    assert len(out) == 1
