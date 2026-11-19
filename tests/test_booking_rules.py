from app.modules.m8_booking.rules import parse_window, find_conflicts, overlap
from app.errors import BusinessError
import pytest

BASE = {"plan_id": "P1", "vessel_id": "V1", "segment": "S1",
        "start": "2026-11-20T22:00:00Z", "end": "2026-11-20T23:30:00Z"}

def test_parse_window_epoch():
    p = parse_window(dict(BASE))
    assert p["start_s"] < p["end_s"] and p["segment"] == "S1"

def test_parse_requires_timezone():
    bad = dict(BASE, start="2026-11-20T22:00:00")
    with pytest.raises(BusinessError, match="时区"):
        parse_window(bad)

def test_parse_rejects_reversed_window():
    bad = dict(BASE, start="2026-11-20T23:30:00Z", end="2026-11-20T22:00:00Z")
    with pytest.raises(BusinessError, match="结束时刻"):
        parse_window(bad)

def test_half_open_end_touch_is_not_conflict():
    p = parse_window(dict(BASE))
    other = {"segment": "S1", "plan_id": "P2", "vessel_id": "V2",
             "start_s": p["end_s"], "end_s": p["end_s"] + 600}
    assert find_conflicts(p, [other]) == []

def test_overlap_matrix():
    assert overlap(0, 10, 10, 20) is False
    assert overlap(0, 10, 5, 15) is True
    assert overlap(0, 10, -5, 1) is True
    assert find_conflicts(parse_window(dict(BASE)),
                          [{"segment": "OTHER", "plan_id": "X", "vessel_id": "V",
                            "start_s": 0, "end_s": 9999999999}]) == []
