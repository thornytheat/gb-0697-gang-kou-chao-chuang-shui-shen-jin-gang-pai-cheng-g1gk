import os, threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR
from app.errors import BusinessError
from app.modules.m8_booking import service

def plan(plan_id, vessel, start, end, segment="SEG-N1", key=None):
    return {"plan_id": plan_id, "vessel_id": vessel, "segment": segment,
            "start": f"2026-11-20T{start}:00Z", "end": f"2026-11-20T{end}:00Z",
            "request_key": key or f"key-{plan_id}-{start}"}

@pytest.fixture(autouse=True)
def clean_bookings():
    db_file = DATA_DIR / "bookings.sqlite"
    if db_file.exists():
        db_file.unlink()
    yield

def test_first_plan_takes_window_second_gets_receipt():
    ok = service.submit(plan("P1", "V_A", "22:00", "23:30", key="k1"))
    assert ok["status"] == "EFFECTIVE" and ok["version"] == 1
    with pytest.raises(BusinessError) as exc:
        service.submit(plan("P2", "V_B", "23:00", "23:59", key="k2"))
    assert exc.value.code == "PLAN_CONFLICT" and exc.value.status == 409
    hits = exc.value.details["conflicts"]
    assert len(hits) == 1 and hits[0]["plan_id"] == "P1" and hits[0]["vessel_id"] == "V_A"
    board = service.board()
    assert {p["plan_id"]: p["status"] for p in board["plans"]} == {"P1": "EFFECTIVE", "P2": "CONFLICT"}
    assert len(board["effective"]) == 1 and board["effective"][0]["plan_id"] == "P1"

def test_conflict_does_not_overwrite_effective_plan():
    service.submit(plan("P1", "V_A", "22:00", "23:30", key="k1"))
    with pytest.raises(BusinessError):
        service.submit(plan("P2", "V_B", "22:00", "23:30", key="k2"))
    detail = service.get_plan("P1")
    assert detail["active"] is not None and detail["active"]["vessel_id"] == "V_A"
    # P2 只有一条 CONFLICT 版本，从未进入生效表
    p2 = service.get_plan("P2")
    assert p2["active"] is None and [v["status"] for v in p2["versions"]] == ["CONFLICT"]

def test_adjust_only_affected_vessel_resubmits_successfully():
    service.submit(plan("P1", "V_A", "22:00", "23:30", key="k1"))
    with pytest.raises(BusinessError):
        service.submit(plan("P2", "V_B", "23:00", "23:59", key="k2-bad"))
    moved = service.submit(plan("P2", "V_B", "23:30", "23:59", key="k2-fixed"))
    assert moved["status"] == "EFFECTIVE"
    board = service.board()
    assert len(board["effective"]) == 2
    assert {e["plan_id"] for e in board["effective"]} == {"P1", "P2"}

def test_idempotent_retry_does_not_double_occupy():
    body = plan("P1", "V_A", "22:00", "23:30", key="same-key")
    first = service.submit(body)
    second = service.submit(dict(body))  # 断网重连：同键原样重试
    assert first["version"] == second["version"] == 1
    assert len(service.board()["effective"]) == 1
    # 同键的冲突请求也必须可重放，且不新增版本
    with pytest.raises(BusinessError):
        service.submit(plan("P2", "V_B", "22:00", "23:00", key="dup-conflict"))
    for _ in range(3):
        with pytest.raises(BusinessError) as exc:
            service.submit(plan("P2", "V_B", "22:00", "23:00", key="dup-conflict"))
    assert len(service.get_plan("P2")["versions"]) == 1

def test_resubmit_supersedes_own_old_version_but_keeps_history():
    service.submit(plan("P1", "V_A", "22:00", "23:30", key="v1"))
    updated = service.submit(plan("P1", "V_A", "23:30", "23:59", key="v2"))
    assert updated["version"] == 2 and updated["superseded_versions"] == [1]
    versions = service.get_plan("P1")["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["status"] == "SUPERSEDED" and versions[1]["status"] == "EFFECTIVE"

def test_failed_resubmit_keeps_old_effective_version():
    service.submit(plan("P1", "V_A", "22:00", "23:30", key="v1"))
    service.submit(plan("P2", "V_B", "23:30", "23:59", key="p2"))
    with pytest.raises(BusinessError):
        service.submit(plan("P1", "V_A", "23:45", "23:55", key="bad-adjust"))
    active = service.get_plan("P1")["active"]
    assert active["version"] == 1
    statuses = {v["version"]: v["status"] for v in service.get_plan("P1")["versions"]}
    assert statuses == {1: "EFFECTIVE", 2: "CONFLICT"}

def test_cancel_releases_window_and_is_versioned():
    service.submit(plan("P1", "V_A", "22:00", "23:30", key="k1"))
    out = service.cancel({"plan_id": "P1", "request_key": "cancel-1"})
    assert out["status"] == "CANCELLED" and out["version"] == 2
    # 释放后同航段同时刻可被其它计划占用
    again = service.submit(plan("P2", "V_B", "22:00", "23:30", key="k2"))
    assert again["status"] == "EFFECTIVE"
    assert service.get_plan("P1")["active"] is None
    with pytest.raises(BusinessError, match="生效中的占位"):
        service.cancel({"plan_id": "P1", "request_key": "cancel-2"})

def test_concurrent_same_window_only_one_wins():
    barrier = threading.Barrier(2)
    outcomes, errors = [], []
    def worker(payload, store_out, store_err):
        barrier.wait()
        try:
            store_out.append(service.submit(payload))
        except BusinessError as e:
            store_err.append(e)
    t1 = threading.Thread(target=worker, args=(plan("C1", "V1", "22:00", "23:00", key="c1"), outcomes, errors))
    t2 = threading.Thread(target=worker, args=(plan("C2", "V2", "22:00", "23:00", key="c2"), outcomes, errors))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert len(outcomes) == 1 and len(errors) == 1
    assert errors[0].code == "PLAN_CONFLICT"
    assert len(service.board()["effective"]) == 1

def test_concurrent_same_request_key_single_booking():
    db_file = DATA_DIR / "bookings.sqlite"
    if db_file.exists():
        db_file.unlink()
    barrier = threading.Barrier(4)
    results, errors = [], []
    payload = plan("R1", "V1", "22:00", "23:00", key="shared-key")
    def worker():
        barrier.wait()
        try:
            results.append(service.submit(dict(payload)))
        except BusinessError as e:
            errors.append(e)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(results) == 4 and not errors
    assert {r["version"] for r in results} == {1}
    assert len(service.board()["effective"]) == 1

def test_different_segments_or_adjacent_slots_coexist():
    service.submit(plan("P1", "V1", "22:00", "23:00", segment="S1", key="a"))
    service.submit(plan("P2", "V2", "22:00", "23:00", segment="S2", key="b"))
    service.submit(plan("P3", "V3", "23:00", "23:30", segment="S1", key="c"))
    assert len(service.board()["effective"]) == 3

def test_http_endpoints_conflict_and_board():
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    r1 = client.post("/api/8/plans", json=plan("H1", "V1", "22:00", "23:30", key="h1"))
    assert r1.status_code == 200 and r1.json()["status"] == "EFFECTIVE"
    r2 = client.post("/api/8/plans", json=plan("H2", "V2", "22:30", "23:00", key="h2"))
    assert r2.status_code == 409
    body = r2.json()
    assert body["error"] == "PLAN_CONFLICT"
    assert body["details"]["conflicts"][0]["plan_id"] == "H1"
    board = client.get("/api/8/plans").json()
    assert next(p for p in board["plans"] if p["plan_id"] == "H2")["status"] == "CONFLICT"
    page = client.get("/booking")
    assert page.status_code == 200 and "进港计划占窗工作台" in page.text
