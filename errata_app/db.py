"""SQLite 持久化层。

凭据失效规则（核心业务约束）：
  - 凭据保存原值、拟改值以及所引用的合计（引用时的值快照）；
  - 一旦 *被引用* 的合计被修改，相关凭据立即标记为失效（同一事务内完成）；
  - 未被任何凭据引用的合计更正，不影响已有凭据。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    grid_json   TEXT NOT NULL,
    rows_n      INTEGER NOT NULL,
    cols_n      INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS margins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    entry       INTEGER NOT NULL,           -- 0..rows-1 行合计；rows..rows+cols-1 列合计
    kind        TEXT NOT NULL,              -- 'row' | 'col'
    idx         INTEGER NOT NULL,           -- 行号/列号（0 起）
    value       INTEGER,                    -- NULL 表示尚未录入
    updated_at  TEXT NOT NULL,
    UNIQUE(dataset_id, entry)
);

CREATE TABLE IF NOT EXISTS vouchers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id    INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    target_row    INTEGER NOT NULL,
    target_col    INTEGER NOT NULL,
    original_value INTEGER NOT NULL,
    proposed_value INTEGER NOT NULL,
    status        TEXT NOT NULL,            -- 'valid' | 'invalid'
    result_json   TEXT NOT NULL,            -- solver.analyze 的完整结论
    created_at    TEXT NOT NULL,
    invalidated_at TEXT
);

CREATE TABLE IF NOT EXISTS voucher_margins (
    voucher_id   INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
    margin_id    INTEGER NOT NULL REFERENCES margins(id) ON DELETE CASCADE,
    value_at_cite INTEGER NOT NULL,         -- 引用瞬间的合计值（凭据快照）
    PRIMARY KEY (voucher_id, margin_id)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --------------------------------------------------------------------------- #
# 数据集
# --------------------------------------------------------------------------- #
def create_dataset(conn, name: str, grid: list[list[int]]) -> int:
    cur = conn.execute(
        "INSERT INTO datasets(name, grid_json, rows_n, cols_n, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, json.dumps(grid, ensure_ascii=False), len(grid), len(grid[0]), now_iso()),
    )
    dataset_id = cur.lastrowid
    r, c = len(grid), len(grid[0])
    for k in range(r + c):
        kind = "row" if k < r else "col"
        idx = k if k < r else k - r
        conn.execute(
            "INSERT INTO margins(dataset_id, entry, kind, idx, value, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (dataset_id, k, kind, idx, now_iso()),
        )
    conn.commit()
    return dataset_id


def get_dataset(conn, dataset_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM datasets WHERE id = ?", (dataset_id,)
    ).fetchone()


def list_datasets(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM datasets ORDER BY id DESC"
    ).fetchall()


def get_margins(conn, dataset_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM margins WHERE dataset_id = ? ORDER BY entry",
        (dataset_id,),
    ).fetchall()


def update_margins(conn, dataset_id: int, values_by_entry: dict[int, int | None]) -> dict:
    """更新合计，并把引用了被改动合计的有效凭据在同一事务内立即标失效。

    仅当新值与现值不同（含 NULL↔非 NULL）才算“修改”。
    返回 {'changed': [...], 'invalidated_voucher_ids': [...]}。
    """
    changed_entries: list[int] = []
    conn.execute("BEGIN")
    try:
        for entry, new_value in values_by_entry.items():
            row = conn.execute(
                "SELECT id, value FROM margins WHERE dataset_id = ? AND entry = ?",
                (dataset_id, entry),
            ).fetchone()
            if row is None:
                raise ValueError(f"合计条目 {entry} 不存在")
            if row["value"] != new_value:
                conn.execute(
                    "UPDATE margins SET value = ?, updated_at = ? WHERE id = ?",
                    (new_value, now_iso(), row["id"]),
                )
                changed_entries.append(entry)

        invalidated: list[int] = []
        if changed_entries:
            placeholders = ",".join("?" for _ in changed_entries)
            hits = conn.execute(
                f"""
                SELECT DISTINCT v.id
                FROM vouchers v
                JOIN voucher_margins vm ON vm.voucher_id = v.id
                JOIN margins m ON m.id = vm.margin_id
                WHERE v.dataset_id = ? AND v.status = 'valid'
                  AND m.entry IN ({placeholders})
                """,
                (dataset_id, *changed_entries),
            ).fetchall()
            invalidated = [h["id"] for h in hits]
            for vid in invalidated:
                conn.execute(
                    "UPDATE vouchers SET status = 'invalid', invalidated_at = ? "
                    "WHERE id = ?",
                    (now_iso(), vid),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"changed": changed_entries, "invalidated_voucher_ids": invalidated}


# --------------------------------------------------------------------------- #
# 凭据
# --------------------------------------------------------------------------- #
def save_voucher(conn, dataset_id: int, target_row: int, target_col: int,
                 original_value: int, proposed_value: int, result: dict) -> int:
    cur = conn.execute(
        "INSERT INTO vouchers(dataset_id, target_row, target_col, original_value, "
        "proposed_value, status, result_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'valid', ?, ?)",
        (dataset_id, target_row, target_col, original_value, proposed_value,
         json.dumps(result, ensure_ascii=False), now_iso()),
    )
    voucher_id = cur.lastrowid
    for cited in result.get("cited_entries", []):
        margin = conn.execute(
            "SELECT id FROM margins WHERE dataset_id = ? AND entry = ?",
            (dataset_id, cited["entry"]),
        ).fetchone()
        conn.execute(
            "INSERT INTO voucher_margins(voucher_id, margin_id, value_at_cite) "
            "VALUES (?, ?, ?)",
            (voucher_id, margin["id"], cited["value"]),
        )
    conn.commit()
    return voucher_id


def get_voucher(conn, voucher_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM vouchers WHERE id = ?", (voucher_id,)).fetchone()


def get_voucher_citations(conn, voucher_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT vm.value_at_cite, m.entry, m.kind, m.idx, m.value AS current_value,
               m.updated_at
        FROM voucher_margins vm
        JOIN margins m ON m.id = vm.margin_id
        WHERE vm.voucher_id = ?
        ORDER BY m.entry
        """,
        (voucher_id,),
    ).fetchall()


def list_vouchers(conn, dataset_id: int | None = None) -> list[sqlite3.Row]:
    if dataset_id is None:
        return conn.execute(
            "SELECT * FROM vouchers ORDER BY id DESC"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM vouchers WHERE dataset_id = ? ORDER BY id DESC",
        (dataset_id,),
    ).fetchall()
