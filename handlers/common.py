"""Базовые хендлеры: /start, /help, поиск счётчика по умолчанию (текст/фото),
показ карточки счётчика и запись в блокнот с комментарием."""

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

import db
import local_db
import search
from callbacks import SaveNotebookCb, SkipReadingCb
from constants import KEYWORDS
from formatting import fmt_serial
from states import Form

logger = logging.getLogger(__name__)

router = Router()

# In-memory store: user_id → pending notebook entry_id
_pending_entry: dict[int, int] = {}

WELCOME = (
    "<b>Бот поиска и контроля счётчиков электроэнергии и воды</b>\n\n"
    "Отправьте <b>фото со штрих-кодом</b> или введите:\n"
    "  • серийный номер\n"
    "  • код адреса в формате <b>N.М</b> — например <code>26.1</code>\n\n"
    "<b>Команды:</b>\n"
    "  /notebook — блокнот\n"
    "  /clear — очистить блокнот\n"
    "  /checkup — режим проверки\n"
    "  /checkups — журнал проверок\n"
    "  /reading, <b>Показания</b> — показания на дату\n"
    "  /period, <b>Период</b> — посуточный отчёт (до 90 дней)\n"
    "  /monthly, <b>Месяцы</b> — помесячный отчёт (до 5 лет)\n"
    "  /help — эта справка\n\n"
    "Ключевые слова: <b>Блокнот</b>, <b>Очисти</b>, <b>Проверка</b>, <b>Проверки</b>, "
    "<b>Показания</b>, <b>Период</b>, <b>Месяцы</b>."
)


# ── /start, /help ─────────────────────────────────────────────────────────────


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())


# ── Фото ──────────────────────────────────────────────────────────────────────


@router.message(F.photo, StateFilter(None))
async def handle_photo(message: Message, bot: Bot) -> None:
    barcode = await search.read_barcode_from_message(message, bot)
    if barcode is None:
        return
    logger.info("Barcode decoded: %s", barcode)
    row = await search.find_by_barcode(barcode)
    await _show_counter(message, row, barcode)


# ── Текст: серийный номер / код N.M ───────────────────────────────────────────


@router.message(
    F.text,
    ~F.text.startswith("/"),
    F.text.func(lambda t: bool(t) and t.strip().lower() not in KEYWORDS),
    StateFilter(None),
)
async def handle_text(message: Message) -> None:
    serial = message.text.strip()
    if not serial:
        return
    row = await search.find_by_text(serial)
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
        f"<b>Серийный номер:</b> <code>{fmt_serial(row['SerialNumber'])}</code>\n"
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


# ── FSM: ввод комментария к записи блокнота ──────────────────────────────────


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
