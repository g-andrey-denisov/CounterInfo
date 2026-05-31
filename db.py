from datetime import datetime, timedelta

import aiomysql
from config import settings

_pool: aiomysql.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await aiomysql.create_pool(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db=settings.DB_NAME,
        charset="utf8mb4",
        minsize=1,
        maxsize=5,
        autocommit=True,
    )


async def close_pool() -> None:
    if _pool:
        _pool.close()
        await _pool.wait_closed()


_COUNTER_QUERY = """
    SELECT
        c.Obj_Id_Counter,
        c.Name,
        c.SerialNumber,
        c.State,
        cons.Consumption,
        cons.UpdateTime
    FROM counter c
    LEFT JOIN consumption cons
        ON c.Obj_Id_Counter = cons.Obj_Id_Counter
    {where}
    ORDER BY cons.UpdateTime DESC
    LIMIT 1
"""


async def _query_one(sql: str, params: tuple) -> dict | None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def get_counter_by_serial(serial: str) -> dict | None:
    """Точное совпадение по серийному номеру (без учёта ведущих нулей)."""
    sql = _COUNTER_QUERY.format(
        where="WHERE TRIM(LEADING '0' FROM c.SerialNumber) = %s"
    )
    return await _query_one(sql, (serial.lstrip("0"),))


async def get_counter_by_barcode(barcode: str) -> dict | None:
    """Поиск счётчика, чей серийный номер является подстрокой штрих-кода (без учёта ведущих нулей)."""
    sql = _COUNTER_QUERY.format(
        where="WHERE INSTR(%s, TRIM(LEADING '0' FROM c.SerialNumber)) > 0"
    )
    return await _query_one(sql, (barcode.lstrip("0"),))


async def get_counter_by_name_code(left: str, right: str) -> dict | None:
    """Поиск по точному коду N.M внутри скобок в поле Name."""
    pattern = f'\\([^0-9)]*{left}\\.{right}[^0-9)]*\\)'
    sql = _COUNTER_QUERY.format(where="WHERE c.Name REGEXP %s")
    return await _query_one(sql, (pattern,))


async def get_all_counters() -> list[dict]:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT SerialNumber, Name, State FROM counter")
            return await cur.fetchall()


async def get_consumption_around_date(counter_id: int, date_str: str) -> dict:
    """
    Возвращает dict с ключами:
      before   — последняя запись ДО начала date_str (или None)
      on_date  — последняя запись ЗА date_str (или None)
      after    — первая запись ПОСЛЕ date_str, только если on_date is None (или None)
    """
    day_start = f"{date_str} 00:00:00"
    day_end = (
        datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d 00:00:00")

    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT Consumption, UpdateTime FROM consumption "
                "WHERE Obj_Id_Counter = %s AND UpdateTime < %s "
                "ORDER BY UpdateTime DESC LIMIT 1",
                (counter_id, day_start),
            )
            before = await cur.fetchone()

            await cur.execute(
                "SELECT Consumption, UpdateTime FROM consumption "
                "WHERE Obj_Id_Counter = %s AND UpdateTime >= %s AND UpdateTime < %s "
                "ORDER BY UpdateTime DESC LIMIT 1",
                (counter_id, day_start, day_end),
            )
            on_date = await cur.fetchone()

            after = None
            if not on_date:
                await cur.execute(
                    "SELECT Consumption, UpdateTime FROM consumption "
                    "WHERE Obj_Id_Counter = %s AND UpdateTime >= %s "
                    "ORDER BY UpdateTime ASC LIMIT 1",
                    (counter_id, day_end),
                )
                after = await cur.fetchone()

    return {"before": before, "on_date": on_date, "after": after}


async def get_consumption_for_period(
    counter_id: int, start_date: str, end_date: str
) -> dict:
    """
    Возвращает данные для построения таблицы показаний за период:
      pre        — последняя запись ДО начала периода
      in_period  — все записи в пределах периода (ASC)
      post       — первая запись ПОСЛЕ окончания периода
    """
    period_start = f"{start_date} 00:00:00"
    period_end = (
        datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d 00:00:00")

    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT Consumption, UpdateTime FROM consumption "
                "WHERE Obj_Id_Counter = %s AND UpdateTime < %s "
                "ORDER BY UpdateTime DESC LIMIT 1",
                (counter_id, period_start),
            )
            pre = await cur.fetchone()

            await cur.execute(
                "SELECT Consumption, UpdateTime FROM consumption "
                "WHERE Obj_Id_Counter = %s AND UpdateTime >= %s AND UpdateTime < %s "
                "ORDER BY UpdateTime ASC",
                (counter_id, period_start, period_end),
            )
            in_period = list(await cur.fetchall())

            await cur.execute(
                "SELECT Consumption, UpdateTime FROM consumption "
                "WHERE Obj_Id_Counter = %s AND UpdateTime >= %s "
                "ORDER BY UpdateTime ASC LIMIT 1",
                (counter_id, period_end),
            )
            post = await cur.fetchone()

    return {"pre": pre, "in_period": in_period, "post": post}
