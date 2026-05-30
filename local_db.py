import aiosqlite
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "local.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notebook (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    ts             TEXT    NOT NULL,
    serial_number  TEXT    NOT NULL,
    name           TEXT,
    state          TEXT,
    db_consumption TEXT,
    comment        TEXT
);

CREATE TABLE IF NOT EXISTS counter_cache (
    serial_number    TEXT PRIMARY KEY,
    name             TEXT,
    state            TEXT,
    last_scan_date   TEXT,
    last_consumption TEXT,
    last_state       TEXT
);

CREATE TABLE IF NOT EXISTS cache_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


async def init_local_db() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ── Counter cache ─────────────────────────────────────────────────────────────

async def should_sync_today() -> bool:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT value FROM cache_meta WHERE key = 'last_sync'"
        ) as cur:
            row = await cur.fetchone()
    return row is not None and row[0] == today


async def sync_counters(counters: list[dict]) -> None:
    today = date.today().isoformat()
    serials = [c["SerialNumber"] for c in counters]
    async with aiosqlite.connect(DB_PATH) as db:
        # Upsert актуальных счётчиков
        for c in counters:
            await db.execute(
                """
                INSERT INTO counter_cache (serial_number, name, state)
                VALUES (?, ?, ?)
                ON CONFLICT(serial_number) DO UPDATE SET
                    name  = excluded.name,
                    state = excluded.state
                """,
                (c["SerialNumber"], c["Name"], c.get("State")),
            )
        # Удалить счётчики, которых больше нет в основной БД
        if serials:
            placeholders = ",".join("?" * len(serials))
            await db.execute(
                f"DELETE FROM counter_cache WHERE serial_number NOT IN ({placeholders})",
                serials,
            )
        else:
            await db.execute("DELETE FROM counter_cache")

        await db.execute(
            """
            INSERT INTO cache_meta (key, value) VALUES ('last_sync', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (today,),
        )
        await db.commit()


async def update_counter_scan(
    serial: str, consumption: str | None, state: str | None
) -> None:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE counter_cache
            SET last_scan_date   = ?,
                last_consumption = ?,
                last_state       = ?
            WHERE serial_number = ?
            """,
            (today, consumption, state, serial),
        )
        await db.commit()


# ── Notebook ──────────────────────────────────────────────────────────────────

async def add_notebook_entry(
    user_id: int,
    serial: str,
    name: str | None,
    state: str | None,
    db_consumption: str | None,
) -> int:
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO notebook
                (user_id, ts, serial_number, name, state, db_consumption)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, ts, serial, name, state, db_consumption),
        )
        await db.commit()
        return cur.lastrowid


async def update_notebook_comment(entry_id: int, comment: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE notebook SET comment = ? WHERE id = ?",
            (comment, entry_id),
        )
        await db.commit()


async def get_notebook(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM notebook WHERE user_id = ? ORDER BY id",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def clear_notebook(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notebook WHERE user_id = ?", (user_id,))
        await db.commit()
