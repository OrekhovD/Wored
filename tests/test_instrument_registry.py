"""Unit tests for the V3 instrument registry (ТЗ V3 §4, MC-01 foundation)."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from instrument_registry import (
    InstrumentNotFound,
    InstrumentRegistry,
    RegistryError,
    default_registry_path,
    load_registry,
)


REPO_REGISTRY = Path(__file__).resolve().parent.parent / "config" / "instrument_registry.json"


def test_repo_default_file_parses() -> None:
    registry = InstrumentRegistry.from_path(REPO_REGISTRY)
    spec = registry.get("htx:linear-swap:BTC-USDT")
    assert spec.venue == "htx"
    assert spec.market_type == "linear-swap"
    assert spec.contract_code == "BTC-USDT"
    assert spec.settlement_currency == "USDT"
    assert spec.price_tick == Decimal("0.1")
    assert spec.quantity_step == Decimal("0.001")
    assert spec.contract_size == Decimal("0.0001")
    assert spec.fee_schedule_version == "htx-linear-v1"
    assert "1m" in spec.supported_periods
    assert "1d" in spec.supported_periods
    assert spec.is_perpetual is True


def test_default_registry_path_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORED_INSTRUMENT_REGISTRY", str(REPO_REGISTRY))
    assert default_registry_path() == REPO_REGISTRY


def test_load_registry_caches_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORED_INSTRUMENT_REGISTRY", str(REPO_REGISTRY))
    first = load_registry(force_reload=True)
    second = load_registry()
    assert first is second


def test_get_unknown_key_raises_not_found() -> None:
    registry = InstrumentRegistry.from_path(REPO_REGISTRY)
    with pytest.raises(InstrumentNotFound):
        registry.get("htx:spot:BTC-USDT")


def test_registry_rejects_empty() -> None:
    with pytest.raises(RegistryError, match="at least one instrument"):
        InstrumentRegistry.from_payload({"schema_version": 1, "instruments": []})


def test_registry_rejects_duplicate_keys() -> None:
    payload = json.loads(REPO_REGISTRY.read_text(encoding="utf-8"))
    payload["instruments"] = payload["instruments"] * 2
    with pytest.raises(RegistryError, match="duplicate"):
        InstrumentRegistry.from_payload(payload)


def test_registry_rejects_bad_schema_version() -> None:
    payload = json.loads(REPO_REGISTRY.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    with pytest.raises(RegistryError, match="schema_version"):
        InstrumentRegistry.from_payload(payload)


def test_registry_rejects_key_body_mismatch() -> None:
    payload = json.loads(REPO_REGISTRY.read_text(encoding="utf-8"))
    payload["instruments"][0]["contract_code"] = "ETH-USDT"  # key still says BTC-USDT
    with pytest.raises(RegistryError, match="contradicts"):
        InstrumentRegistry.from_payload(payload)


def test_registry_rejects_non_positive_tick() -> None:
    payload = copy.deepcopy(json.loads(REPO_REGISTRY.read_text(encoding="utf-8")))
    payload["instruments"][0]["price_tick"] = "0"
    with pytest.raises(RegistryError, match="price_tick"):
        InstrumentRegistry.from_payload(payload)


def test_registry_rejects_unknown_status() -> None:
    payload = copy.deepcopy(json.loads(REPO_REGISTRY.read_text(encoding="utf-8")))
    payload["instruments"][0]["status"] = "on_fire"
    with pytest.raises(RegistryError, match="status"):
        InstrumentRegistry.from_payload(payload)


def test_registry_rejects_unsupported_period() -> None:
    payload = copy.deepcopy(json.loads(REPO_REGISTRY.read_text(encoding="utf-8")))
    payload["instruments"][0]["supported_periods"] = ["1m", "73s"]
    with pytest.raises(RegistryError, match="unsupported"):
        InstrumentRegistry.from_payload(payload)


def test_public_dict_serializes_decimal_as_string() -> None:
    registry = InstrumentRegistry.from_path(REPO_REGISTRY)
    spec = registry.get("htx:linear-swap:BTC-USDT")
    payload = spec.public_dict()
    assert isinstance(payload["price_tick"], str)
    assert isinstance(payload["quantity_step"], str)
    assert isinstance(payload["contract_size"], str)
    assert isinstance(payload["supported_periods"], list)
