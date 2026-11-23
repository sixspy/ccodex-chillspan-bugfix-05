"""互证封批红测（先红后修）。

甲：已正式封批 → 互证在批正式计数零改并拒累加，不得双写。
乙：未封批 → 仅落影子草案，正式互证零改；封批态复核通过后原子晋升，否则草案回滚。
纪要：互证封批纪要一旦记入，当日不再改口；偏离记互证封批偏航并拒写。
争用：两路会话同封批抢对账锁，后到只留痕不写互证行。
同源：监控台 / 互证 / 封存 / 曲线 / 占用五处读口同一封批态。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import pytest

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _chamber(c: TestClient, h: dict, code: str) -> dict:
    return next(
        x
        for x in c.get("/api/chambers", headers=h).json()["items"]
        if x["chamber_code"] == code
    )


def _new_batch(
    c: TestClient, h: dict, code: str, chamber_code: str = "CK-HN-03"
) -> dict:
    ch = _chamber(c, h, chamber_code)
    probe = c.get(f"/api/probes?chamber_id={ch['id']}", headers=h).json()["items"][0]
    mid = (float(ch["target_min"]) + float(ch["target_max"])) / 2
    r = c.post(
        "/api/batches",
        headers=h,
        json={
            "chamber_id": ch["id"],
            "probe_id": probe["id"],
            "cargo_ref": "互证封批夹具",
            "batch_code": code,
            "temp_points": [{"t": "08:00", "c": mid}, {"t": "09:00", "c": mid}],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _seal(c: TestClient, h: dict, batch_id: int) -> None:
    r = c.post("/api/seals", headers=h, json={"batch_id": batch_id})
    assert r.status_code == 200, r.text


def _count(sql: str, args: tuple = ()) -> int:
    with connect() as conn:
        return int(conn.execute(sql, args).fetchone()["c"])


def test_a_sealed_batch_refuses_in_batch_accumulation():
    """甲路径：封批后在批计数回落，互证在批正式计数零改，重复拉不双写。"""
    c = TestClient(app)
    h = _auth(c)
    code = "TB-RED-A-001"
    base = c.get("/api/reconcile/CK-HN-03", headers=h).json()["active_batches"]
    batch = _new_batch(c, h, code)
    created = c.get("/api/reconcile/CK-HN-03", headers=h).json()["active_batches"]
    assert created == base + 1

    _seal(c, h, batch["id"])
    after = c.get("/api/reconcile/CK-HN-03", headers=h).json()
    assert after["active_batches"] == base, "封批后不得仍计在批"
    assert after["sealed_batches"] == 1

    proof = c.get(f"/api/proof/batch/{code}", headers=h).json()
    assert proof["sealed"] is True
    assert proof["seal_state"] == "已封批"
    assert proof["active_counted"] is False
    assert proof["branch"] == "甲"
    assert proof["formal_accumulated"] == 0

    first = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "甲拉取"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["refused_accumulation"] is True
    assert body["in_batch_formal"] == 0
    assert body["state"] == "正式"

    second = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "重复"})
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True, "封存批禁止双写"
    # 甲路径零写：不落正式互证行。
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE batch_id=? AND mode='甲'",
            (batch["id"],),
        )
        == 0
    )
    assert (
        c.get(f"/api/proof/batch/{code}", headers=h).json()["formal_accumulated"] == 0
    )


def test_b_shadow_draft_promotes_atomically_or_rolls_back():
    """乙路径：草案正式零改；复核未过回滚；封批后复核通过原子晋升。"""
    c = TestClient(app)
    h = _auth(c)
    code = "TB-RED-B-001"
    base = c.get("/api/reconcile/CK-HN-03", headers=h).json()["active_batches"]
    batch = _new_batch(c, h, code)

    draft = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "影子草案"})
    assert draft.status_code == 200, draft.text
    d1 = draft.json()["draft_id"]
    assert draft.json()["state"] == "影子草案"
    p = c.get(f"/api/proof/batch/{code}", headers=h).json()
    assert p["formal_accumulated"] == 0, "草案不得抢先累加正式互证"
    assert p["draft_count"] == 1
    assert p["branch"] == "乙"
    assert c.get("/api/reconcile/CK-HN-03", headers=h).json()["active_batches"] == base + 1

    bad = c.post(f"/api/proof/draft/{d1}/promote", headers=h, json={"review": "驳回"})
    assert bad.status_code == 409, "复核未过不得晋升"
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE id=? AND state='已回滚'",
            (d1,),
        )
        == 1
    )
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE batch_id=? AND state='正式'",
            (batch["id"],),
        )
        == 0
    )

    d2 = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "再落草案"}).json()[
        "draft_id"
    ]
    _seal(c, h, batch["id"])
    ok = c.post(f"/api/proof/draft/{d2}/promote", headers=h, json={"review": "通过"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "正式"

    p2 = c.get(f"/api/proof/batch/{code}", headers=h).json()
    assert p2["formal_accumulated"] == 1
    assert p2["draft_count"] == 0
    assert p2["active_counted"] is False
    assert p2["seal_state"] == "已封批"
    assert c.get("/api/reconcile/CK-HN-03", headers=h).json()["active_batches"] == base

    minutes = p2["minutes"]
    assert minutes and minutes["branch"] == "乙" and minutes["statement"]
    again = c.get(f"/api/proof/batch/{code}", headers=h).json()["minutes"]
    assert again["statement"] == minutes["statement"], "封批纪要当日不得改口"
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_seal_minutes WHERE batch_id=?",
            (batch["id"],),
        )
        == 1
    )

    mixed = c.post(f"/api/proof/batch/{code}", headers=h, json={"mode": "甲", "note": "甲混入"})
    assert mixed.status_code == 409
    assert "偏航" in mixed.json()["detail"]
    assert (
        c.post(f"/api/proof/draft/{d2}/promote", headers=h, json={"review": "通过"})
        .status_code
        == 409
    )
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE batch_id=? AND mode='甲'",
            (batch["id"],),
        )
        == 0
    )


def test_c_same_sealed_batch_pull_contends_trace_only():
    """争用：两路会话同封批抢对账锁，后到只留痕不写互证行。"""
    c1 = TestClient(app)
    c2 = TestClient(app)
    h1 = _auth(c1, "admin")
    h2 = _auth(c2, "ops")
    code = "TB-RED-C-001"
    batch = _new_batch(c1, h1, code)
    _seal(c1, h1, batch["id"])
    lock_key = f"proof:{batch['id']}"
    assert (
        c1.post(
            "/api/locks/acquire", headers=h1, json={"lock_key": lock_key}
        ).status_code
        == 200
    )
    r = c2.post(f"/api/proof/batch/{code}", headers=h2, json={"note": "争用拉取"})
    assert r.status_code == 409
    assert "争用" in r.json()["detail"]
    cont = c2.get("/api/contention", headers=h2).json()["items"]
    assert any(x["lock_key"] == lock_key and x["actor"] == "ops" for x in cont)
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE batch_id=?", (batch["id"],)
        )
        == 0
    )
    proof = c1.get(f"/api/proof/batch/{code}", headers=h1).json()
    assert proof["formal_accumulated"] == 0
    assert proof["active_counted"] is False
    assert proof["seal_state"] == "已封批"


def test_d_cross_branch_and_minutes_deviation_refused():
    """甲乙不得混用；偏离纪要记偏航并拒写。"""
    c = TestClient(app)
    h = _auth(c)
    code = "TB-RED-D-001"
    batch = _new_batch(c, h, code)
    d = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "先乙"}).json()
    assert d["state"] == "影子草案"
    _seal(c, h, batch["id"])

    # 已落乙草案、批次已封批：再拉是“待复核”，不是偏航、也不写互证行。
    pending = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "后拉"})
    assert pending.status_code == 409
    assert "待封批态复核" in pending.json()["detail"]
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_deviations WHERE batch_id=?",
            (batch["id"],),
        )
        == 0
    )

    # 显式改走甲 = 混用，记偏航并拒写。
    mixed = c.post(f"/api/proof/batch/{code}", headers=h, json={"mode": "甲"})
    assert mixed.status_code == 409
    assert "偏航" in mixed.json()["detail"]
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_deviations WHERE batch_id=?",
            (batch["id"],),
        )
        == 1
    )
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE batch_id=? AND mode='甲'",
            (batch["id"],),
        )
        == 0
    )

    ok = c.post(
        f"/api/proof/draft/{d['draft_id']}/promote", headers=h, json={"review": "通过"}
    )
    assert ok.status_code == 200, ok.text
    minutes = c.get(f"/api/proof/batch/{code}", headers=h).json()["minutes"]
    assert minutes["branch"] == "乙"

    again = c.post(f"/api/proof/batch/{code}", headers=h, json={"mode": "甲", "note": "再混"})
    assert again.status_code == 409
    assert "偏航" in again.json()["detail"]
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_deviations WHERE batch_id=?",
            (batch["id"],),
        )
        == 2
    )
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_seal_minutes WHERE batch_id=?",
            (batch["id"],),
        )
        == 1
    )
    idem = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "重复回读"})
    assert idem.status_code == 200
    assert idem.json()["deduplicated"] is True
    assert idem.json()["formal_accumulated"] == 1


def test_e_five_views_share_one_seal_state():
    """监控台 / 互证 / 封存 / 曲线 / 占用五处读口同源。"""
    c = TestClient(app)
    h = _auth(c)
    code = "TB-RED-E-001"
    batch = _new_batch(c, h, code)
    _seal(c, h, batch["id"])
    assert (
        c.post("/api/spectrum/export", headers=h, json={"batch_id": batch["id"]})
        .status_code
        == 200
    )

    dash = c.get("/api/dashboard", headers=h).json()
    recon = c.get("/api/reconcile", headers=h).json()
    assert dash["active_batches"] == sum(i["active_batches"] for i in recon["items"])
    assert dash["sealed_batches"] == sum(i["sealed_batches"] for i in recon["items"])

    ch = _chamber(c, h, "CK-HN-03")
    detail = c.get(f"/api/chambers/{ch['id']}", headers=h).json()
    item = next(i for i in recon["items"] if i["chamber_code"] == "CK-HN-03")
    assert detail["active_batch_count"] == item["active_batches"]
    assert detail["sealed_batch_count"] == item["sealed_batches"]
    assert detail["sealed_batch_count"] >= 1

    row = next(
        b
        for b in c.get("/api/batches", headers=h).json()["items"]
        if b["batch_code"] == code
    )
    assert row["seal_state"] == "已封批" and row["active_counted"] is False

    proof = c.get(f"/api/proof/batch/{code}", headers=h).json()
    assert proof["seal_state"] == "已封批" and proof["active_counted"] is False

    seal_row = next(
        s for s in c.get("/api/seals", headers=h).json()["items"] if s["batch_code"] == code
    )
    assert seal_row["seal_state"] == "已封批"

    sp_row = next(
        x
        for x in c.get("/api/spectrum", headers=h).json()["items"]
        if x["batch_code"] == code
    )
    assert sp_row["seal_state"] == "已封批" and sp_row["active_counted"] is False


def test_f_failed_promotion_releases_reconcile_lock():
    """失败路径也须释放对账锁，不得卡住后续争用判定。"""
    c = TestClient(app)
    h = _auth(c)
    code = "TB-RED-F-001"
    batch = _new_batch(c, h, code)
    draft = c.post(f"/api/proof/batch/{code}", headers=h, json={"note": "草案"}).json()
    r = c.post(
        f"/api/proof/draft/{draft['draft_id']}/promote",
        headers=h,
        json={"review": "通过"},
    )
    assert r.status_code == 409
    locks = c.get("/api/locks", headers=h).json()["items"]
    assert not any(x["lock_key"] == f"proof:{batch['id']}" for x in locks)
    assert (
        _count(
            "SELECT COUNT(*) c FROM proof_records WHERE id=? AND state='已回滚'",
            (draft["draft_id"],),
        )
        == 1
    )

