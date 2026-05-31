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

CREATE TABLE IF NOT EXISTS checkup (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at    TEXT    NOT NULL,
    user_id       INTEGER NOT NULL,
    user_name     TEXT,
    serial_number TEXT    NOT NULL,
    name          TEXT,
    status        TEXT    NOT NULL,
    reading       TEXT,
    comment       TEXT
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


async def notebook_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM notebook WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def clear_notebook(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM notebook WHERE user_id = ?", (user_id,))
        await db.commit()


# ── Checkup ───────────────────────────────────────────────────────────────────

async def add_checkup_entry(
    user_id: int,
    user_name: str | None,
    serial: str,
    name: str | None,
    status: str,
    reading: str | None,
    comment: str | None,
) -> int:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO checkup
                (checked_at, user_id, user_name, serial_number, name, status, reading, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (checked_at, user_id, user_name, serial, name, status, reading, comment),
        )
        await db.commit()
        return cur.lastrowid


async def get_unchecked_counters(offset: int = 0, limit: int = 15) -> tuple[list[dict], int]:
    """Возвращает (страница, всего) для счётчиков без проверки или с давней проверкой.
    Исключает счётчики с 'снят'/'демонтирован' в названии."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT
                cc.serial_number,
                cc.name,
                MAX(ch.checked_at) AS last_check
            FROM counter_cache cc
            LEFT JOIN checkup ch ON cc.serial_number = ch.serial_number
            GROUP BY cc.serial_number, cc.name
            ORDER BY cc.name ASC
        """) as cur:
            rows = await cur.fetchall()

    excluded = {"снят", "демонтирован"}
    filtered = [
        dict(r) for r in rows
        if not any(kw in (r["name"] or "").lower() for kw in excluded)
    ]
    total = len(filtered)
    return filtered[offset : offset + limit], total


def _raw_to_iso(raw: str) -> str:
    """Нормализует дату в строку ISO YYYY-MM-DD независимо от исходного формата."""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


async def get_checkup_dates(limit: int = 5) -> list[str]:
    """Возвращает последние N уникальных дат проверки (ISO YYYY-MM-DD), новые первыми.
    Обрабатывает смешанные форматы дат в таблице."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT substr(checked_at, 1, 10) FROM checkup"
        ) as cur:
            rows = await cur.fetchall()

    seen: set[str] = set()
    result: list[str] = []
    # нормализуем все даты и сортируем по убыванию
    iso_dates = sorted(
        {_raw_to_iso(row[0]) for row in rows},
        reverse=True,
    )
    for d in iso_dates:
        if d not in seen:
            seen.add(d)
            result.append(d)
            if len(result) >= limit:
                break
    return result


async def get_checkups_by_date(
    date_iso: str, offset: int = 0, limit: int = 10
) -> tuple[list[dict], int]:
    """Возвращает (страница, итого) проверок за указанную дату.
    Ищет записи в обоих форматах (ISO и старом дд.мм.гггг)."""
    try:
        old_fmt = datetime.strptime(date_iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        old_fmt = date_iso
    iso_p = f"{date_iso}%"
    old_p = f"{old_fmt}%"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COUNT(*) FROM checkup WHERE checked_at LIKE ? OR checked_at LIKE ?",
            (iso_p, old_p),
        ) as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        async with db.execute(
            """
            SELECT * FROM checkup
            WHERE checked_at LIKE ? OR checked_at LIKE ?
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (iso_p, old_p, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows], total
