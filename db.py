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


async def get_counter_by_name_code(left: int, right: int) -> dict | None:
    """Поиск по шаблону N.M внутри скобок в поле Name (без учёта ведущих нулей)."""
    pattern = f'\\(0*{left}\\.0*{right}\\)'
    sql = _COUNTER_QUERY.format(where="WHERE c.Name REGEXP %s")
    return await _query_one(sql, (pattern,))


async def get_all_counters() -> list[dict]:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT SerialNumber, Name, State FROM counter")
            return await cur.fetchall()
