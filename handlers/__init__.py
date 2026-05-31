"""Сборка корневого роутера из всех модулей-обработчиков.

Порядок включения важен: при совпадении побеждает первый зарегистрированный
обработчик. Команды/ключевые слова разделов идут раньше, обработчик поиска по
умолчанию (catch-all текст/фото в `common`) — последним, чтобы не перехватывать
ввод других сценариев. Порядок reading → period → monthly и блоков проверки
повторяет исходный приоритет из старого bot.py.
"""

from aiogram import Router

from . import (
    calendars,
    checkup,
    checkups,
    common,
    monthly,
    notebook,
    period,
    reading,
)


def build_router() -> Router:
    root = Router()
    root.include_router(notebook.router)
    root.include_router(checkups.router)
    root.include_router(checkup.router)
    root.include_router(reading.router)
    root.include_router(period.router)
    root.include_router(monthly.router)
    root.include_router(calendars.router)
    root.include_router(common.router)  # catch-all — последним
    return root
