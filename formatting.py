"""Форматтеры и парсеры: показания, даты, серийные номера, разбивка длинных сообщений."""

import calendar
import re
from datetime import datetime

from aiogram.types import Message


def fmt_serial(s: str) -> str:
    return s.zfill(8)


def diff_str(before: str, reading: str) -> str:
    """Целочисленная разница reading - before; '—' если одно из значений не число."""
    try:
        return str(int(reading) - int(before))
    except (ValueError, TypeError):
        return "—"


def fmt_consumption(rec: dict | None) -> str:
    if not rec:
        return "нет"
    c = rec.get("Consumption")
    if c is None:
        return "нет"
    return str(round(c))


def fmt_iso_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso_date


def parse_input_date(text: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def fmt_check_date(iso_str: str | None) -> str:
    if not iso_str:
        return "нет проверки"
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso_str, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return iso_str


def plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 19:
        return many
    r = n % 10
    if r == 1:
        return one
    if 2 <= r <= 4:
        return few
    return many


def parse_date_clamped(text: str) -> tuple[str, bool] | None:
    """Парсит дату с автокоррекцией числа до последнего дня месяца.
    Возвращает (iso_date, was_clamped) или None.
    """
    direct = parse_input_date(text.strip())
    if direct:
        return direct, False

    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', text.strip())
    if m:
        try:
            day, month = int(m.group(1)), int(m.group(2))
            yr = int(m.group(3))
            if len(m.group(3)) == 2:
                yr += 2000 if yr < 70 else 1900
            if 1 <= month <= 12 and day >= 1:
                max_day = calendar.monthrange(yr, month)[1]
                if day > max_day:
                    return datetime(yr, month, max_day).strftime("%Y-%m-%d"), True
        except (ValueError, OverflowError):
            pass

    return None


def parse_period_input(text: str) -> tuple[str, str, list[str]] | None:
    """Извлекает две даты из строки вида 'дд.мм.гггг - дд.мм.гггг'.
    Возвращает (start_iso, end_iso, corrections) или None.
    """
    matches = re.findall(r'\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}', text)
    if len(matches) < 2:
        return None

    res_s = parse_date_clamped(matches[0])
    res_e = parse_date_clamped(matches[1])
    if not res_s or not res_e:
        return None

    start_iso, s_clamped = res_s
    end_iso, e_clamped = res_e
    corrections: list[str] = []
    if s_clamped:
        corrections.append(f"{matches[0]} → {fmt_iso_date(start_iso)}")
    if e_clamped:
        corrections.append(f"{matches[1]} → {fmt_iso_date(end_iso)}")
    return start_iso, end_iso, corrections


def parse_year_input(text: str) -> tuple[int, int] | None:
    if re.fullmatch(r'\d{4}', text):
        y = int(text)
        return y, y
    m = re.fullmatch(r'(\d{4})\s*[-–—]\s*(\d{4})', text)
    if not m:
        m = re.fullmatch(r'(\d{4})\s+(\d{4})', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


async def send_long(message: Message, text: str) -> None:
    limit = 4096
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        await message.answer(text[:split_at], parse_mode="HTML")
        text = text[split_at:].lstrip("\n")
    if text:
        await message.answer(text, parse_mode="HTML")
