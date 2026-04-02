# -*- coding: utf-8 -*-
"""SQLite persistent storage for orders and PDF records.

Replaces the in-memory dict store so that payment state survives
process restarts (crash, deploy) within the same container.
"""
import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger(__name__)

_default_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "rusit_orders.db")
DB_PATH     = os.environ.get("DB_PATH", _default_db)
ORDER_TTL = 24 * 3600   # 24 h
PDF_TTL   = 1  * 3600   # 1 h


# ── connection helper ────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


# ── schema ───────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                inv              TEXT PRIMARY KEY,
                case_data        TEXT DEFAULT '{}',
                analysis_result  TEXT DEFAULT '{}',
                paid             INTEGER DEFAULT 0,
                charge_id        TEXT DEFAULT '',
                charge_status    TEXT DEFAULT '',
                retry_count      INTEGER DEFAULT 0,
                refunded         INTEGER DEFAULT 0,
                access_token     TEXT DEFAULT '',
                idempotency_key  TEXT DEFAULT '',
                ts               REAL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pdfs (
                sid              TEXT PRIMARY KEY,
                demand_path      TEXT DEFAULT '',
                petition_path    TEXT DEFAULT '',
                download_token   TEXT DEFAULT '',
                ts               REAL DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pdfs_ts   ON pdfs(ts)")
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_idem "
            "ON orders(idempotency_key) WHERE idempotency_key != ''"
        )
    logger.info("DB 초기화 완료: %s", DB_PATH)


# ── orders ───────────────────────────────────────────────────────

def save_order(inv, case_data, analysis_result,
               access_token="", idempotency_key=""):
    with _conn() as c:
        c.execute(
            "INSERT INTO orders "
            "(inv, case_data, analysis_result, access_token, idempotency_key, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (inv,
             json.dumps(case_data, ensure_ascii=False),
             json.dumps(analysis_result, ensure_ascii=False),
             access_token, idempotency_key, time.time()),
        )


def get_order(inv):
    with _conn() as c:
        row = c.execute("SELECT * FROM orders WHERE inv = ?", (inv,)).fetchone()
    if not row:
        return None
    return {
        "inv":             row["inv"],
        "case_data":       json.loads(row["case_data"]),
        "analysis_result": json.loads(row["analysis_result"]),
        "_paid":           bool(row["paid"]),
        "charge_id":       row["charge_id"],
        "charge_status":   row["charge_status"],
        "retry_count":     row["retry_count"],
        "refunded":        bool(row["refunded"]),
        "access_token":    row["access_token"],
        "_ts":             row["ts"],
    }


def update_order(inv, **fields):
    allowed = {"paid", "charge_id", "charge_status",
               "retry_count", "refunded"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [inv]
    with _conn() as c:
        c.execute(f"UPDATE orders SET {cols} WHERE inv = ?", vals)


def find_by_idempotency_key(key):
    """Return order dict if idempotency_key already used, else None."""
    if not key:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT inv FROM orders WHERE idempotency_key = ?", (key,)
        ).fetchone()
    if row:
        return get_order(row["inv"])
    return None


# ── pdfs ─────────────────────────────────────────────────────────

def save_pdf(sid, demand_path, petition_path, download_token):
    with _conn() as c:
        c.execute(
            "INSERT INTO pdfs "
            "(sid, demand_path, petition_path, download_token, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, demand_path, petition_path, download_token, time.time()),
        )


def get_pdf(sid):
    with _conn() as c:
        row = c.execute("SELECT * FROM pdfs WHERE sid = ?", (sid,)).fetchone()
    if not row:
        return None
    return {
        "demand_path":    row["demand_path"],
        "petition_path":  row["petition_path"],
        "download_token": row["download_token"],
        "_ts":            row["ts"],
    }


# ── cleanup ──────────────────────────────────────────────────────

def evict_expired():
    now = time.time()
    with _conn() as c:
        # PDF 파일 삭제
        rows = c.execute(
            "SELECT sid, demand_path, petition_path FROM pdfs WHERE ? - ts > ?",
            (now, PDF_TTL),
        ).fetchall()
        for r in rows:
            for p in (r["demand_path"], r["petition_path"]):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            logger.debug("PDF 만료 제거: %s", r["sid"])

        c.execute("DELETE FROM orders WHERE ? - ts > ?", (now, ORDER_TTL))
        c.execute("DELETE FROM pdfs   WHERE ? - ts > ?", (now, PDF_TTL))
