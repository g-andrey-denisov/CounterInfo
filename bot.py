"""Точка входа бота: настройка логирования, инициализация БД и запуск polling.

Вся логика обработчиков вынесена в модули (`handlers/`, `keyboards.py`,
`formatting.py`, `search.py` и др.); здесь остаётся только сборка приложения.
"""

import asyncio
import logging
import logging.handlers
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

import db
import local_db
from config import settings
from handlers import build_router
from middlewares import AccessMiddleware

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).parent / "logs"
LOG_KEEP_DAYS = 14
_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Консоль
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(sh)

    # Файл с ежедневной ротацией и удалением старых логов
    fh = logging.handlers.TimedRotatingFileHandler(
        filename=LOG_DIR / "bot.log",
        when="midnight",
        interval=1,
        backupCount=LOG_KEEP_DAYS,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)


async def main() -> None:
    _setup_logging()

    await local_db.init_local_db()
    await db.init_pool()
    logger.info("DB pool ready")

    if not await local_db.should_sync_today():
        counters = await db.get_all_counters()
        await local_db.sync_counters(counters)
        logger.info("Counter cache synced: %d entries", len(counters))

    bot = Bot(token=settings.BOT_TOKEN)
    base_commands = [
        BotCommand(command="help",     description="Справка"),
        BotCommand(command="notebook", description="Блокнот"),
        BotCommand(command="clear",    description="Очистить блокнот"),
        BotCommand(command="checkup",  description="Режим проверки"),
        BotCommand(command="checkups", description="Журнал проверок"),
        BotCommand(command="reading",  description="Показания на дату"),
        BotCommand(command="period",   description="Показания за период"),
        BotCommand(command="monthly",  description="Помесячный отчёт"),
    ]
    await bot.set_my_commands(base_commands)

    # Персональное меню администратора: базовые команды + управление доступом.
    admin_commands = base_commands + [
        BotCommand(command="users", description="Управление пользователями"),
    ]
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception:
            logger.warning("Не удалось задать команды для администратора %s", admin_id)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(build_router())

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await db.close_pool()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
