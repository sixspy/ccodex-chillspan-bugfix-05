"""仓间配额 vs 在批差额对账 + 互证封批链。

口径：在批一律经 batch_state（排除已封批与作废），互证与封批态绑定。

甲乙分叉钉死，同一批次不得混用：
- 甲：批次已正式封批 → 互证在批正式计数零改并拒累加（不双写）。
- 乙：未封批 → 只落影子草案，正式互证零改；封批态复核通过后原子晋升，
  否则草案回滚。

互证封批纪要按（批次, 封批日）唯一；一旦记入当日不再改口。偏离纪要的
写入记互证封批偏航并拒写。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from batch_state import state_of
from db import connect, now_iso, write_audit
from locks import release, require_lock
from proof_minutes import STATEMENT_A, STATEMENT_B, minutes_of, record_minutes

router = APIRouter(tags=["reconcile"])

BRANCH_A = "甲"
BRANCH_B = "乙"
STATE_FORMAL = "正式"
STATE_DRAFT = "影子草案"
STATE_ROLLED = "已回滚"


def _chamber_recon(conn, chamber) -> dict:
    cid = chamber["id"]
    quota = int(chamber["quota_batches"])
    rows = conn.execute(
        "SELECT status FROM temp_batches WHERE chamber_id=?", (cid,)
    ).fetchall()
    active = sum(1 for r in rows if state_of(r)["active_counted"])
    sealed = sum(1 for r in rows if state_of(r)["sealed"])
    gap = quota - active
    ok = active <= quota
    return {
        "chamber_id": cid,
        "chamber_code": chamber["chamber_code"],
        "region": chamber["region"],
        "quota_batches": quota,
        "active_batches": active,
        "sealed_batches": sealed,
        "quota_left": gap,
        "balanced": ok,
        "proof": {
            "formula": "active_batches <= quota_batches（在批不含已封批/作废）",
            "left": active,
            "right": quota,
        },
    }


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        chambers = conn.execute("SELECT * FROM cold_chambers ORDER BY id").fetchall()
        items = [_chamber_recon(conn, c) for c in chambers]
        bad = [i for i in items if not i["balanced"]]
        return {
            "items": items,
            "summary": {
                "total": len(items),
                "balanced": len(items) - len(bad),
                "unbalanced": len(bad),
                "active_total": sum(i["active_batches"] for i in items),
                "sealed_total": sum(i["sealed_batches"] for i in items),
            },
        }


@router.get("/api/reconcile/{chamber_code}")
def reconcile_one(chamber_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        ch = conn.execute(
            "SELECT * FROM cold_chambers WHERE chamber_code=?", (chamber_code,)
        ).fetchone()
        if not ch:
            raise HTTPException(404, "仓间不存在")
        return _chamber_recon(conn, ch)


def _records(conn, batch_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM proof_records WHERE batch_id=? ORDER BY id", (batch_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _branch_of(records: list[dict]) -> str | None:
    for r in records:
        if r["state"] != STATE_ROLLED:
            return r["mode"]
    return None


def _minutes(conn, batch_id: int) -> dict | None:
    return minutes_of(conn, batch_id)


def _proof_summary(conn, batch: dict) -> dict:
    records = _records(conn, batch["id"])
    formal = [r for r in records if r["state"] == STATE_FORMAL]
    drafts = [r for r in records if r["state"] == STATE_DRAFT]
    sealed_state = state_of(batch)
    # 无互证行时按封批态预钉路径：已封批=甲，未封批=乙；一旦落行则钉死。
    branch = _branch_of(records) or (
        BRANCH_A if sealed_state["sealed"] else BRANCH_B
    )
    return {
        "batch_code": batch["batch_code"],
        "status": batch["status"],
        "generation": batch["generation"],
        "sealed": sealed_state["sealed"],
        "seal_state": sealed_state["seal_state"],
        "active_counted": sealed_state["active_counted"],
        "branch": branch,
        "formal_accumulated": len(formal),
        "draft_count": len(drafts),
        # 正式互证只在封批后（乙晋升）出现，故在批口径的正式累计恒为 0。
        "in_batch_formal": sum(1 for r in formal if not sealed_state["sealed"]),
        "minutes": _minutes(conn, batch["id"]),
        "records": records,
    }


def _load_batch(conn, batch_code: str) -> dict:
    batch = conn.execute(
        "SELECT * FROM temp_batches WHERE batch_code=?", (batch_code,)
    ).fetchone()
    if not batch:
        raise HTTPException(404, "批次不存在")
    return dict(batch)


def _refuse_deviation(conn, batch_id: int, user: str, detail: str) -> None:
    conn.execute(
        """INSERT INTO proof_deviations(batch_id,actor,detail,created_at)
           VALUES (?,?,?,?)""",
        (batch_id, user, detail, now_iso()),
    )
    # 拒写也要留下偏航痕迹。
    conn.commit()
    raise HTTPException(409, f"互证封批偏航：{detail}，拒写")


@router.get("/api/proof/batch/{batch_code}")
def batch_proof(batch_code: str, authorization: AuthHeader = None):
    """批次互证只读：封批态同源 + 甲乙分叉 + 纪要。"""
    require_user(authorization)
    with connect() as conn:
        batch = _load_batch(conn, batch_code)
        summary = _proof_summary(conn, batch)
        open_exc = conn.execute(
            """SELECT COUNT(*) c FROM excursion_cursors
               WHERE batch_id=? AND status='开放'""",
            (batch["id"],),
        ).fetchone()["c"]
        exports = conn.execute(
            """SELECT COUNT(*) c FROM spectrum_exports
               WHERE batch_id=? AND status='已导出'""",
            (batch["id"],),
        ).fetchone()["c"]
        spectrum_ok = (exports >= 1) if summary["sealed"] else True
        summary.update(
            {
                "open_excursions": open_exc,
                "spectrum_exports": exports,
                "excursion_closed": open_exc == 0,
                "spectrum_matches_seal": spectrum_ok,
                "proof": {
                    "formula": "sealed => spectrum>=1 and open_excursions==0",
                    "sealed": summary["sealed"],
                    "open_excursions": open_exc,
                    "exports": exports,
                },
            }
        )
        return summary


@router.post("/api/proof/batch/{batch_code}")
def pull_proof(batch_code: str, authorization: AuthHeader = None, body: dict | None = None):
    """拉取互证：抢对账锁；甲拒累加，乙落草案；纪要当日不改口。"""
    user = require_writer(authorization)
    body = body or {}
    with connect() as conn:
        batch = _load_batch(conn, batch_code)
        lock_key = f"proof:{batch['id']}"
        require_lock(conn, lock_key, user)
        try:
            summary = _proof_summary(conn, batch)
            sealed = summary["sealed"]
            requested = body.get("mode")
            # 路径在读取处已按封批态钉死（已封批=甲，未封批=乙），此处只校验混用。
            branch = summary["branch"]
            if requested and requested != branch:
                _refuse_deviation(
                    conn,
                    batch["id"],
                    user,
                    f"批次已钉死{branch}路径，不得改用{requested}",
                )

            if branch == BRANCH_A:
                if not sealed:
                    _refuse_deviation(
                        conn, batch["id"], user, "甲路径要求批次已正式封批"
                    )
                # 甲：正式互证零改，仅回零计数与拒累加，不写互证行（不双写）。
                already = _minutes(conn, batch["id"]) is not None
                record_minutes(conn, batch, BRANCH_A, STATEMENT_A)
                write_audit(
                    conn,
                    user,
                    "proof_pull",
                    batch_code,
                    {"branch": BRANCH_A, "refused_accumulation": True},
                )
                conn.commit()
                return {
                    "batch_code": batch_code,
                    "state": STATE_FORMAL,
                    "branch": BRANCH_A,
                    "refused_accumulation": True,
                    "in_batch_formal": 0,
                    "formal_accumulated": 0,
                    "deduplicated": already,
                    "proof_id": None,
                }

            # 乙路径：未封批只落影子草案，正式互证零改。
            formal = [
                r for r in summary["records"] if r["state"] == STATE_FORMAL
            ]
            if formal:
                # 已晋升的正式互证：再拉只回读，不双写、不再累加。
                record_minutes(conn, batch, BRANCH_B, STATEMENT_B)
                conn.commit()
                return {
                    "batch_code": batch_code,
                    "state": STATE_FORMAL,
                    "branch": BRANCH_B,
                    "refused_accumulation": True,
                    "deduplicated": True,
                    "in_batch_formal": 0,
                    "formal_accumulated": len(formal),
                }
            if summary["draft_count"]:
                # 草案待复核：不是偏航，只提示先复核。
                raise HTTPException(409, "已有影子草案待封批态复核")
            if sealed:
                _refuse_deviation(
                    conn, batch["id"], user, "乙路径草案须在封批前落定"
                )
            cur = conn.execute(
                """INSERT INTO proof_records(batch_id,mode,state,note,created_at)
                   VALUES (?,?,?,?,?)""",
                (batch["id"], BRANCH_B, STATE_DRAFT, body.get("note", ""), now_iso()),
            )
            write_audit(
                conn,
                user,
                "proof_draft",
                batch_code,
                {"branch": BRANCH_B, "draft_id": cur.lastrowid},
            )
            conn.commit()
            return {
                "batch_code": batch_code,
                "draft_id": cur.lastrowid,
                "state": STATE_DRAFT,
                "branch": BRANCH_B,
                "formal_accumulated": 0,
                "in_batch_formal": 0,
            }
        finally:
            # 失败路径也要释放对账锁：先落已写入的痕迹，再删锁并提交。
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/proof/draft/{draft_id}/promote")
def promote_draft(
    draft_id: int, authorization: AuthHeader = None, body: dict | None = None
):
    """草案晋升：须封批态复核通过且批次已封批，原子转正；否则回滚。"""
    user = require_writer(authorization)
    body = body or {}
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM proof_records WHERE id=?", (draft_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "草案不存在")
        if row["state"] != STATE_DRAFT:
            raise HTTPException(409, "草案已处理")
        batch = conn.execute(
            "SELECT * FROM temp_batches WHERE id=?", (row["batch_id"],)
        ).fetchone()
        if not batch:
            raise HTTPException(404, "批次不存在")
        lock_key = f"proof:{batch['id']}"
        require_lock(conn, lock_key, user)
        try:
            sealed = state_of(batch)["sealed"]
            review = body.get("review")
            if review != "通过" or not sealed:
                conn.execute(
                    """UPDATE proof_records SET state=?, reviewed_at=? WHERE id=?""",
                    (STATE_ROLLED, now_iso(), draft_id),
                )
                write_audit(
                    conn, user, "proof_rollback", batch["batch_code"], {"draft_id": draft_id}
                )
                conn.commit()
                raise HTTPException(
                    409,
                    "封批态复核未通过或批次未封批，草案已回滚",
                )
            now = now_iso()
            conn.execute(
                """UPDATE proof_records SET state=?, reviewed_at=?, promoted_at=?
                   WHERE id=?""",
                (STATE_FORMAL, now, now, draft_id),
            )
            record_minutes(conn, batch, BRANCH_B, STATEMENT_B)
            write_audit(
                conn,
                user,
                "proof_promote",
                batch["batch_code"],
                {"draft_id": draft_id},
            )
            conn.commit()
            return {
                "draft_id": draft_id,
                "batch_code": batch["batch_code"],
                "state": STATE_FORMAL,
                "branch": BRANCH_B,
            }
        finally:
            # 失败路径也要释放对账锁：先落已写入的痕迹，再删锁并提交。
            release(conn, lock_key, user)
            conn.commit()
