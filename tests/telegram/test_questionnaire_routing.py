"""Q-20 (фаза P4) — голый payload не должен становиться ответом модели.

Ответ анкеты и аргументы тикета — единственные строки этого бота, которые
пользователь набирает как перечень ``key=value``.  Без команды они не проходят
ни один ``Command``-фильтр и прилетали в AI-чат, который отвечал на *payload*
как на вопрос — сгенерированными торговыми числами.  Это тот же дефект, что
Q-15 нашёл на опечатке в команде, поэтому правило живёт рядом.

Тест проверяет три вещи: отказ называет ровно ту команду, которая действительно
принимает эти токены; модель не вызвана ни разу; обычная проза по-прежнему
доходит до AI-маршрутизатора (правило не проглатывает разговор).
"""

from __future__ import annotations

import pytest
from conftest import make_message

from ai import router as ai_router
from handlers import chat as chat_router
from handlers import telegram_surface as surface
from services import paper_orders as deals
from services import simulation_proposals as proposals

ANSWER = (
    "goal=risk_control duration=24 side=both involvement=supervised "
    "manual=100 auto=50 maxloss=30 hours=any"
)
TICKET = "side=buy risk=10 stop=82000 tp=84000 lev=5"


@pytest.fixture
def ai_spy(monkeypatch):
    """Любой вызов модели — записывается; тесты требуют пустого списка."""
    calls: list[str] = []

    async def _route(message, context=None):
        calls.append("route_request")
        return "🤖 вход, точка, стоп, цель: уверенность 90%"

    async def _raw(tier_key, system_prompt, user_content, **kwargs):
        calls.append("call_raw")
        return "{}"

    monkeypatch.setattr(chat_router, "route_request", _route)
    monkeypatch.setattr(ai_router, "call_raw", _raw)
    return calls


# ─── форма принадлежит поверхности, владеют ключи — сервисам ───────────────


def test_shape_rule_is_shared_not_duplicated() -> None:
    assert surface.looks_like_key_values(ANSWER) is True
    assert surface.looks_like_key_values(TICKET) is True
    # проза, даже со знаком «=» внутри, payload'ом не считается
    assert surface.looks_like_key_values("что думаешь про btc?") is False
    assert surface.looks_like_key_values("side=buy и risk=10") is False
    assert surface.looks_like_key_values("=10") is False
    assert surface.looks_like_key_values("") is False


def test_ownership_thresholds_are_the_services_own_business() -> None:
    assert proposals.recognised_answer_keys(ANSWER) == 8
    # двух полей мало: «side=both duration=24» — ещё не анкета
    assert proposals.recognised_answer_keys("side=both duration=24") == 0
    assert deals.recognises_ticket_args(TICKET) is True
    assert deals.recognises_ticket_args("side=buy tp=84000") is False


def test_the_refused_command_is_the_command_that_accepts_those_tokens() -> None:
    """Подсказка отказа обязана быть исполнимой, а не декоративной."""
    template = proposals.answer_template()
    assert template.startswith("/plans propose ")

    _raw, errors = proposals.parse_proposal_answer(template.split(None, 2)[2])
    assert errors == []

    preview = deals.usage_preview(instrument=deals.tradable_instrument())
    assert preview.startswith("/deal preview ")
    body = preview.split(None, 2)[2]
    assert deals.parse_ticket_args(body)["stop_price"] == "63000"


# ─── маршрутизация: отказ вместо модели ────────────────────────────────────


async def test_bare_questionnaire_is_refused_without_a_model_call(ai_spy) -> None:
    message = make_message(text=ANSWER)

    await chat_router.cmd_chat(message, principal=None)

    assert ai_spy == []
    text = message.sent[0]["text"]
    assert "/plans propose" in text
    assert "анкета так не принимается" in text
    assert "Ни одно предложение не создано" in text


async def test_bare_ticket_args_are_refused_without_a_model_call(ai_spy) -> None:
    message = make_message(text=TICKET)

    await chat_router.cmd_chat(message, principal=None)

    assert ai_spy == []
    text = message.sent[0]["text"]
    assert "/deal preview" in text
    assert "тикет так не собирается" in text
    assert "заявка не записана" in text


async def test_a_half_typed_answer_still_proves_ownership(ai_spy) -> None:
    """Часть полей есть, команда потерялась по дороге — отказ тот же."""
    message = make_message(text="goal=risk_control duration=24 involvement=supervised")

    await chat_router.cmd_chat(message, principal=None)

    assert ai_spy == []
    assert "распознано полей: 3" in message.sent[0]["text"]


async def test_prose_still_reaches_the_ai_fallback(ai_spy) -> None:
    """Правило не должно отнимать у модели обычный разговор."""
    message = make_message(text="почему биткоин так двигается в последние дни")

    await chat_router.cmd_chat(message, principal=None)

    assert ai_spy == ["route_request"]


async def test_mistyped_command_is_refused_before_the_payload_rule(ai_spy) -> None:
    """Q-15 и Q-20 не пересекаются: слэш — это команда, а не payload."""
    message = make_message(text="/plansl propose goal=risk_control duration=24")

    await chat_router.cmd_chat(message, principal=None)

    assert ai_spy == []
    assert "Команда не распознана" in message.sent[0]["text"]


async def test_the_payload_rule_runs_before_the_wait_message(ai_spy) -> None:
    """Никакого «🤔 Анализирую…» при отказе: модель не вызывалась вообще."""
    message = make_message(text=ANSWER)

    await chat_router.cmd_chat(message, principal=None)

    assert all("Анализирую" not in sent["text"] for sent in message.sent)
    assert len(message.sent) == 1


def test_refusal_helper_never_invents_a_number() -> None:
    """В тексте отказа только команда и имена ключей — никаких значений."""
    text = chat_router._bare_payload_refusal("goal=x duration=1 side=both")

    assert text is not None
    assert "82668" not in text and "manual=100" not in text
