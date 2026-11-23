"""批次封批态唯一读口：在批 / 已封批 / 作废三态同源。

监控台、互证、封存、温谱曲线、仓间占用五处视图一律经此判定，
禁止各服务自行拼 status 条件造成口径错位。
"""
from __future__ import annotations

SEALED = "已封批"
VOID = "作废"
ACTIVE_STATUSES = ("草稿", "已入库", "超温处理中")

# 在批口径：仅排除已封批与作废；草稿与超温处理中仍属在批。
ACTIVE_SQL = "status NOT IN ('已封批','作废')"


def is_sealed(row) -> bool:
    return row is not None and row["status"] == SEALED


def is_active(row) -> bool:
    return row is not None and row["status"] in ACTIVE_STATUSES


def state_of(row) -> dict:
    """统一封批态摘要，供各读口回传同源字段。"""
    sealed = is_sealed(row)
    return {
        "status": row["status"],
        "sealed": sealed,
        "active_counted": is_active(row),
        "seal_state": SEALED if sealed else (VOID if row["status"] == VOID else "在批"),
    }


def state_from_status(status: str) -> dict:
    """仅有状态字符串时的同源映射（封存/温谱等关联读口）。

    不回传 status：关联读口自身有 status（如温谱“已导出”），避免覆写。
    """
    sealed = status == SEALED
    return {
        "sealed": sealed,
        "active_counted": status in ACTIVE_STATUSES,
        "seal_state": SEALED if sealed else (VOID if status == VOID else "在批"),
    }


def active_count(conn, chamber_id: int) -> int:
    row = conn.execute(
        f"""SELECT COUNT(*) c FROM temp_batches
            WHERE chamber_id=? AND {ACTIVE_SQL}""",
        (chamber_id,),
    ).fetchone()
    return int(row["c"])


def sealed_count(conn, chamber_id: int) -> int:
    row = conn.execute(
        """SELECT COUNT(*) c FROM temp_batches
           WHERE chamber_id=? AND status=?""",
        (chamber_id, SEALED),
    ).fetchone()
    return int(row["c"])


def active_total(conn) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) c FROM temp_batches WHERE {ACTIVE_SQL}"
        ).fetchone()["c"]
    )
