"""Authorization layer for the Telegram transport (ТЗ Telegram Bot V1 §3).

``principal`` maps a numeric ``from_user.id`` to a server-derived role and
``owner_id``; ``guards`` applies that mapping to every update before any router
sees it.  Nothing in this package may read message content for authorization.
"""
