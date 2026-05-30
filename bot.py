import asyncio
import logging
import logging.handlers
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Awaitable

import zxingcpp
from PIL import Image

from aiogram import BaseMiddleware, Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from aiogram.filters.callback_data import CallbackData

from config import settings
import db
import local_db

logger = logging.getLogger(__name__)

# ── FSM ───────────────────────────────────────────────────────────────────────


class Form(StatesGroup):
    waiting_comment = State()


# ── Callback data ─────────────────────────────────────────────────────────────


class SaveNotebookCb(CallbackData, prefix="snb"):
    serial: str


class SkipReadingCb(CallbackData, prefix="srp"):
    entry_id: int


class ClearConfirmCb(CallbackData, prefix="clr"):
    yes: int  # 1 = yes, 0 = no


# ── In-memory store: user_id → pending notebook entry_id ─────────────────────

_pending_entry: dict[int, int] = {}


# ── Access middleware ─────────────────────────────────────────────────────────


class AccessMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if (
            user
            and settings.ALLOWED_USER_IDS
            and user.id not in settings.ALLOWED_USER_IDS
        ):
            if isinstance(event, Message):
                await event.answer("Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён.", show_alert=True)
            return
        return await handler(event, data)


# ── Router ────────────────────────────────────────────────────────────────────

router = Router()

_KEYWORDS = frozenset(["блокнот", "очисти"])

WELCOME = (
    "<b>Бот поиска счётчиков электроэнергии</b>\n\n"
    "Отправьте <b>фото со штрих-кодом</b> счётчика "
    "или введите <b>серийный номер</b> вручную.\n\n"
    "<b>Команды:</b>\n"
    "  /notebook — открыть блокнот\n"
    "  /clear — очистить блокнот\n"
    "  /help — справка\n\n"
    "Также работают слова: <b>Блокнот</b> и <b>Очисти</b>."
)

# ── /start, /help ─────────────────────────────────────────────────────────────


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, parse_mode="HTML")


# ── /notebook + слово "Блокнот" ───────────────────────────────────────────────


@router.message(Command("notebook", "note"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "блокнот"))
async def cmd_notebook(message: Message, state: FSMContext) -> None:
    await state.clear()
    entries = await local_db.get_notebook(message.from_user.id)
    if not entries:
        await message.answer("Блокнот пуст.")
        return
    n = len(entries)
    lines = [f"<b>Блокнот ({n} {_plural(n, 'запись', 'записи', 'записей')})</b>\n"]
    for i, e in enumerate(entries, 1):
        comment = e["comment"] or "не указано"
        lines.append(
            f"<b>#{i}</b>  {e['ts']}\n"
            f"Серийный:   <code>{_fmt_serial(e['serial_number'])}</code>\n"
            f"Название:   {e['name'] or '—'}\n"
            f"Состояние:  {e['state'] or '—'}\n"
            f"Показ.(БД): {e['db_consumption'] or '—'}\n"
            f"Комментарий: {comment}\n"
            "──────────────────────"
        )
    await _send_long(message, "\n".join(lines))


# ── /clear + слово "Очисти" ───────────────────────────────────────────────────


@router.message(Command("clear"))
@router.message(F.text.func(lambda t: bool(t) and t.strip().lower() == "очисти"))
async def cmd_clear(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, очистить", callback_data=ClearConfirmCb(yes=1).pack()
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=ClearConfirmCb(yes=0).pack()
                ),
            ]
        ]
    )
    await message.answer("Очистить блокнот? Все записи будут удалены.", reply_markup=kb)


@router.callback_query(ClearConfirmCb.filter())
async def cb_clear_confirm(query: CallbackQuery, callback_data: ClearConfirmCb) -> None:
    if callback_data.yes:
        await local_db.clear_notebook(query.from_user.id)
        await query.message.edit_text("Блокнот очищен.", reply_markup=None)
    else:
        await query.message.edit_text("Отменено.", reply_markup=None)
    await query.answer()


# ── Фото ──────────────────────────────────────────────────────────────────────


@router.message(F.photo, StateFilter(None))
async def handle_photo(message: Message, bot: Bot) -> None:
    await message.answer("Распознаю штрих-код...")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    buf = BytesIO()
    await bot.download_file(file.file_path, destination=buf)

    results = zxingcpp.read_barcodes(Image.open(buf))
    if not results:
        await message.answer(
            "Штрих-код не найден на фото.\n"
            "Попробуйте более чёткий снимок или введите номер вручную."
        )
        return

    barcode = results[0].text.strip()
    logger.info("Barcode decoded: %s", barcode)
    row = await db.get_counter_by_barcode(barcode)
    await _show_counter(message, row, barcode)


# ── Текст: серийный номер ─────────────────────────────────────────────────────


@router.message(
    F.text,
    ~F.text.startswith("/"),
    F.text.func(lambda t: bool(t) and t.strip().lower() not in _KEYWORDS),
    StateFilter(None),
)
async def handle_text(message: Message) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await db.get_counter_by_serial(serial)
    await _show_counter(message, row, serial)


# ── Показ информации о счётчике ───────────────────────────────────────────────


async def _show_counter(message: Message, row: dict | None, query: str) -> None:
    if row is None:
        await message.answer(
            f"Счётчик <code>{query}</code> не найден.",
            parse_mode="HTML",
        )
        return

    # Ежедневная синхронизация кэша счётчиков
    if not await local_db.should_sync_today():
        counters = await db.get_all_counters()
        await local_db.sync_counters(counters)

    consumption = str(row["Consumption"]) if row["Consumption"] is not None else "—"
    state_val = str(row["State"]) if row["State"] is not None else "—"
    update_time = (
        row["UpdateTime"].strftime("%d.%m.%Y %H:%M") if row["UpdateTime"] else "—"
    )

    await local_db.update_counter_scan(row["SerialNumber"], consumption, state_val)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записать в блокнот",
                    callback_data=SaveNotebookCb(serial=row["SerialNumber"]).pack(),
                )
            ]
        ]
    )
    await message.answer(
        f"<b>Счётчик найден</b>\n\n"
        f"<b>Серийный номер:</b> <code>{_fmt_serial(row['SerialNumber'])}</code>\n"
        f"<b>Название:</b> {row['Name']}\n"
        f"<b>Состояние:</b> {state_val}\n"
        f"<b>Потребление:</b> {consumption}\n"
        f"<b>Дата обновления:</b> {update_time}",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Callback: сохранить в блокнот ─────────────────────────────────────────────


@router.callback_query(SaveNotebookCb.filter())
async def cb_save_notebook(
    query: CallbackQuery, callback_data: SaveNotebookCb, state: FSMContext
) -> None:
    row = await db.get_counter_by_serial(callback_data.serial)
    if row is None:
        await query.answer("Счётчик не найден.", show_alert=True)
        return

    consumption = str(row["Consumption"]) if row["Consumption"] is not None else None
    state_val = str(row["State"]) if row["State"] is not None else None

    entry_id = await local_db.add_notebook_entry(
        user_id=query.from_user.id,
        serial=row["SerialNumber"],
        name=row["Name"],
        state=state_val,
        db_consumption=consumption,
    )
    _pending_entry[query.from_user.id] = entry_id

    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Записано!")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Пропустить",
                    callback_data=SkipReadingCb(entry_id=entry_id).pack(),
                )
            ]
        ]
    )
    prompt_msg = await query.message.answer(
        "Введите <b>комментарий</b> к записи (показания, заметки и т.д.) "
        "или нажмите «Пропустить»:",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.update_data(prompt_msg_id=prompt_msg.message_id)
    await state.set_state(Form.waiting_comment)


# ── FSM: ввод реальных показаний ──────────────────────────────────────────────


@router.message(Form.waiting_comment, F.text)
async def handle_comment(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    entry_id = _pending_entry.pop(message.from_user.id, None)
    comment = message.text.strip()
    if entry_id and comment:
        await local_db.update_notebook_comment(entry_id, comment)
    await state.clear()
    if prompt_msg_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                reply_markup=None,
            )
        except Exception:
            pass
    await message.answer(
        f"Запись сохранена. Комментарий: <b>{comment or 'не указано'}</b>",
        parse_mode="HTML",
    )


@router.callback_query(SkipReadingCb.filter())
async def cb_skip_reading(query: CallbackQuery, state: FSMContext) -> None:
    _pending_entry.pop(query.from_user.id, None)
    await state.clear()
    await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Пропущено.")
    await query.message.answer("Запись сохранена.")


# ── Вспомогательные ───────────────────────────────────────────────────────────


def _fmt_serial(s: str) -> str:
    return s.zfill(8)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    r = n % 10
    if r == 1:
        return one
    if 2 <= r <= 4:
        return few
    return many


async def _send_long(message: Message, text: str) -> None:
    limit = 4096
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        await message.answer(text[:split_at], parse_mode="HTML")
        text = text[split_at:].lstrip("\n")
    if text:
        await message.answer(text, parse_mode="HTML")


# ── main ──────────────────────────────────────────────────────────────────────


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
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(router)

    try:
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await db.close_pool()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
