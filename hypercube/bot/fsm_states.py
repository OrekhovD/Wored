"""FSM states for interactive flows."""
from aiogram.fsm.state import State, StatesGroup


class AskState(StatesGroup):
    waiting_for_message = State()


class SwitchModelState(StatesGroup):
    waiting_for_model_id = State()


class ModeState(StatesGroup):
    waiting_for_mode = State()
