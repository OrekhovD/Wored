"""
News handler — /news command shows latest crypto news with AI sentiment score.
"""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.news_sentiment import fetch_news, score_sentiment

router = Router()


@router.message(Command("news"))
async def cmd_news(message: Message):
    """Show latest crypto news with AI sentiment score."""
    wait_msg = await message.answer("📰 <i>Собираю новости...</i>")

    try:
        news_items = await fetch_news()
        if not news_items:
            await wait_msg.edit_text("📰 Новости временно недоступны. Попробуй позже.")
            return

        sentiment = await score_sentiment(news_items)

        # Build response
        score = sentiment.get("score", 0.0)
        category = sentiment.get("category", "Neutral")
        rationale = sentiment.get("rationale", "")
        key_factors = sentiment.get("key_factors", [])
        headlines = sentiment.get("headlines", [])

        # Sentiment emoji
        if score > 0.3:
            emoji = "🟢"
        elif score < -0.3:
            emoji = "🔴"
        else:
            emoji = "🟡"

        lines = [f"📰 <b>Крипто-новости</b> {emoji} <b>{category}</b> ({score:+.2f})\n"]

        if rationale:
            lines.append(f"💭 {rationale}\n")

        if key_factors:
            lines.append("🔑 <b>Ключевые факторы:</b>")
            for f in key_factors:
                lines.append(f"  • {f}")
            lines.append("")

        if headlines:
            lines.append("📋 <b>Последние заголовки:</b>")
            for i, h in enumerate(headlines, 1):
                lines.append(f"{i}. {h}")

        await wait_msg.edit_text("\n".join(lines), parse_mode="HTML")

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("News handler error: %s", exc)
        await wait_msg.edit_text("⚠️ Ошибка получения новостей. Попробуй позже.")