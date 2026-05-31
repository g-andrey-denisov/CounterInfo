"""FSM-состояния бота."""

from aiogram.fsm.state import State, StatesGroup


class Form(StatesGroup):
    waiting_comment = State()


class CheckupForm(StatesGroup):
    waiting_query = State()
    waiting_status = State()
    waiting_comment = State()


class CheckupsForm(StatesGroup):
    waiting_date = State()


class ReadingForm(StatesGroup):
    waiting_query = State()
    waiting_date = State()


class PeriodForm(StatesGroup):
    waiting_query = State()
    waiting_period = State()


class MonthlyForm(StatesGroup):
    waiting_query = State()
    waiting_year = State()


class AccessForm(StatesGroup):
    waiting_user_id = State()  # админ вводит Telegram ID нового пользователя
