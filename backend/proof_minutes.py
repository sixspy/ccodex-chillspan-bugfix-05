"""互证封批纪要：按（批次, 封批日）唯一，记入当日不再改口。"""
from __future__ import annotations

from db import now_iso

STATEMENT_A = "甲：已正式封批，互证在批正式计数零改并拒累加"
STATEMENT_B = "乙：影子草案待封批态复核，正式互证零改，复核通过后原子晋升"


def sealed_day(batch) -> str:
    raw = batch["sealed_at"] if batch["sealed_at"] else now_iso()
    return str(raw)[:10]


def record_minutes(conn, batch, branch: str, statement: str) -> None:
    """首记生效；同日已记则保持原文，不改口。"""
    conn.execute(
        """INSERT OR IGNORE INTO proof_seal_minutes
           (batch_id,sealed_date,branch,statement,created_at)
           VALUES (?,?,?,?,?)""",
        (batch["id"], sealed_day(batch), branch, statement, now_iso()),
    )


def minutes_of(conn, batch_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM proof_seal_minutes WHERE batch_id=? ORDER BY id DESC LIMIT 1",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None
