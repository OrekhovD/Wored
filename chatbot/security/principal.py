"""Server-derived principal for Telegram actors (ТЗ Telegram Bot V1 §3).

Deny-by-default: an actor is authorised only by a numeric ``from_user.id`` that
appears in the configured allowlist.  Username, display name and any
client-supplied ``owner_id`` are never authorization input.

The allowlist is read from the environment so the guard works before any
``bot_*`` table exists (P2 slice), and ``BOT_OWNER_IDS`` is the canonical key.
The historical ``TELEGRAM_ADMIN_IDS`` / ``TELEGRAM_ADMIN_ID`` are accepted as
temporary aliases — a deployment that has not migrated yet keeps working, and
the alias in use is reported by name (never values) so operators can see the
drift.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

log = logging.getLogger("chatbot.security")

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"

#: roles that may read; every role below is a reader, only owner/admin mutate
READ_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN, ROLE_VIEWER})
MUTATING_ROLES = frozenset({ROLE_OWNER})

DEFAULT_TIMEZONE = "Asia/Bangkok"


def _parse_ids(raw: str) -> set[int]:
    """Parse a comma-separated list of numeric Telegram IDs.

    Any non-numeric token makes the whole entry unusable rather than silently
    partial: a typo in an owner list must not degrade into a smaller audience.
    """
    tokens = [part.strip() for part in (raw or "").split(",") if part.strip()]
    if not tokens:
        return set()
    try:
        return {int(token) for token in tokens}
    except ValueError:
        log.error("BOT id list is not fully numeric — treating as empty")
        return set()


def _env_ids(*names: str) -> tuple[set[int], str | None]:
    """First non-empty key wins; returns (ids, canonical-name-actually-used)."""
    for name in names:
        value = os.getenv(name, "")
        ids = _parse_ids(value)
        if ids:
            return ids, name
    return set(), None


def owner_ids() -> tuple[set[int], str | None]:
    return _env_ids("BOT_OWNER_IDS", "TELEGRAM_ADMIN_IDS", "TELEGRAM_ADMIN_ID")


def admin_ids() -> tuple[set[int], str | None]:
    return _env_ids("BOT_ADMIN_IDS")


def viewer_ids() -> tuple[set[int], str | None]:
    return _env_ids("BOT_VIEWER_IDS")


def default_timezone() -> str:
    tz = os.getenv("BOT_DEFAULT_TIMEZONE", "").strip()
    return tz or DEFAULT_TIMEZONE


def private_chat_only() -> bool:
    """V1 always restricts to private chats; groups are never a trading surface."""
    return os.getenv("BOT_ALLOW_GROUPS", "false").strip().lower() not in {"1", "true", "yes"}


@dataclass(frozen=True)
class Principal:
    """The authenticated actor for one update.

    ``owner_id`` is derived server-side and is the same value the WebUI and the
    ``paper_trading`` domain compute for the same Telegram account
    (``uuid5(NAMESPACE_URL, "wored:owner:{telegram_user_id}")``), so Telegram
    and WebUI show one owner — never two balances for one person.
    """

    telegram_user_id: int
    role: str
    owner_id: str

    @property
    def is_reader(self) -> bool:
        return self.role in READ_ROLES

    @property
    def may_mutate(self) -> bool:
        return self.role in MUTATING_ROLES


def owner_id_for(telegram_user_id: int) -> str:
    """Deterministic owner identity, shared with ``paper_trading.adapter``."""
    try:
        from paper_trading.adapter import owner_id_from_telegram

        return owner_id_from_telegram(telegram_user_id)
    except Exception:  # pragma: no cover - container import shape, mirrored below
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wored:owner:{telegram_user_id}"))


def resolve(telegram_user_id: int | None) -> Principal | None:
    """Return a Principal or ``None`` (= deny).  Never raises on bad input."""
    if not isinstance(telegram_user_id, int) or telegram_user_id <= 0:
        return None

    owners, _ = owner_ids()
    admins, _ = admin_ids()
    viewers, _ = viewer_ids()

    if telegram_user_id in owners:
        role = ROLE_OWNER
    elif telegram_user_id in admins:
        role = ROLE_ADMIN
    elif telegram_user_id in viewers:
        role = ROLE_VIEWER
    else:
        return None

    return Principal(
        telegram_user_id=telegram_user_id,
        role=role,
        owner_id=owner_id_for(telegram_user_id),
    )


def configuration_warning() -> str | None:
    """Human-readable startup notice about alias use or a missing allowlist.

    Names only — an ID list is not a secret, but it has no place in INFO logs
    either, so only the key name is reported.
    """
    owners, used = owner_ids()
    if not owners:
        return (
            "no Telegram allowlist configured: BOT_OWNER_IDS (alias TELEGRAM_ADMIN_IDS / "
            "TELEGRAM_ADMIN_ID) is empty — every user will be denied"
        )
    if used != "BOT_OWNER_IDS":
        return f"allowlist read from legacy alias {used}; migrate to BOT_OWNER_IDS"
    return None
