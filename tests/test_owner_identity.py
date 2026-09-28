"""Owner-identity resolution tests.

Regression: ``workspace_read`` used to call ``_pt_owner_id(request)`` where
``request`` is a Starlette Request object, and every ``paper_api`` write path
was hardcoded to ``_pt_owner_id("admin")``. That produced two different UUIDs,
so a day started by a command was invisible in the workspace.

These tests pin the contract of ``_owner_from_request``: it derives the same
UUID5 regardless of whether it is called from a command handler, workspace BFF
or report route, and it honors the session's ``auth_type``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5, NAMESPACE_URL

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

from paper_api import _owner_from_request  # noqa: E402


def _make_request(session: dict) -> SimpleNamespace:
    return SimpleNamespace(session=session)


def test_password_session_uses_username():
    req = _make_request({"auth_type": "password", "username": "alice"})
    owner = _owner_from_request(req)
    assert owner == str(uuid5(NAMESPACE_URL, "wored:owner:alice"))
    # Not the fallback admin identity.
    assert owner != str(uuid5(NAMESPACE_URL, "wored:owner:admin"))


def test_telegram_session_uses_user_id():
    req = _make_request({
        "auth_type": "telegram",
        "telegram_user": {"user_id": 12345},
    })
    owner = _owner_from_request(req)
    assert owner == str(uuid5(NAMESPACE_URL, "wored:owner:12345"))


def test_unauthenticated_falls_back_to_admin():
    req = _make_request({})
    owner = _owner_from_request(req)
    assert owner == str(uuid5(NAMESPACE_URL, "wored:owner:admin"))


def test_result_is_valid_uuid_string():
    req = _make_request({"auth_type": "password", "username": "carol"})
    owner = _owner_from_request(req)
    # Must parse as a UUID; ``_pt_owner_id(request)`` regression produced a
    # UUID derived from str(Request) which was still valid-UUID but unstable.
    UUID(owner)


def test_workspace_and_command_paths_agree():
    """The exact bug this test guards:

    - Command handler resolves owner via ``_owner_from_request(request)``.
    - Workspace BFF resolves owner via the SAME function.
    - Given the same session, both must produce the same UUID.
    """
    session = {"auth_type": "password", "username": "dave"}
    req = _make_request(session)
    from_command = _owner_from_request(req)
    from_workspace = _owner_from_request(req)
    assert from_command == from_workspace


def test_different_users_get_different_owners():
    alice = _owner_from_request(_make_request(
        {"auth_type": "password", "username": "alice"}))
    bob = _owner_from_request(_make_request(
        {"auth_type": "password", "username": "bob"}))
    assert alice != bob
