"""Versioned instrument registry for the /api/v3 read-model.

An ``instrument_key`` is never a free-form string: every V3 market, candle,
forecast and session call resolves through this registry.  A key that is not
registered (for example ``htx:spot:BTC-USDT``) returns ``InstrumentNotFound``
so a spot feed can never be silently substituted for perpetual execution data
(ТЗ V3 §0, §4; MC-01).

Load order for the JSON file:
  1. ``WORED_INSTRUMENT_REGISTRY`` env var (explicit override, tests);
  2. ``/config/instrument_registry.json`` (container bind-mount, read-only);
  3. ``<repo>/config/instrument_registry.json`` (host checkout).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_KEY_RE = re.compile(r"^[a-z0-9]+:[a-z0-9\-]+:[A-Z0-9]+\-?[A-Z0-9]*$")
SUPPORTED_PERIODS = {"1m", "5m", "15m", "1h", "4h", "1d"}


class InstrumentNotFound(KeyError):
    """Requested instrument_key is absent from the registry."""


class RegistryError(ValueError):
    """Registry file is malformed or a record violates its contract."""


@dataclass(frozen=True)
class InstrumentSpec:
    instrument_key: str
    venue: str
    market_type: str
    contract_code: str
    price_tick: Decimal
    quantity_step: Decimal
    contract_size: Decimal
    settlement_currency: str
    fee_schedule_version: str
    status: str
    supported_periods: tuple[str, ...]

    @property
    def is_perpetual(self) -> bool:
        return self.venue == "htx" and self.market_type == "linear-swap"

    def public_dict(self) -> dict[str, Any]:
        return {
            "instrument_key": self.instrument_key,
            "venue": self.venue,
            "market_type": self.market_type,
            "contract_code": self.contract_code,
            "price_tick": format(self.price_tick, "f"),
            "quantity_step": format(self.quantity_step, "f"),
            "contract_size": format(self.contract_size, "f"),
            "settlement_currency": self.settlement_currency,
            "fee_schedule_version": self.fee_schedule_version,
            "status": self.status,
            "supported_periods": list(self.supported_periods),
        }


def _positive_decimal(payload: dict[str, Any], field: str) -> Decimal:
    try:
        value = Decimal(str(payload[field]))
    except (InvalidOperation, TypeError, KeyError, ValueError) as exc:
        raise RegistryError(f"{field}: expected a positive decimal") from exc
    if not value.is_finite() or value <= 0:
        raise RegistryError(f"{field}: expected a positive decimal")
    return value


def _parse_spec(raw: Any, index: int) -> InstrumentSpec:
    if not isinstance(raw, dict):
        raise RegistryError(f"instruments[{index}]: expected an object")
    for field in (
        "instrument_key", "venue", "market_type", "contract_code",
        "settlement_currency", "fee_schedule_version", "status",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RegistryError(f"instruments[{index}].{field}: missing string")
    key = raw["instrument_key"].strip()
    if not _KEY_RE.match(key):
        raise RegistryError(f"instruments[{index}].instrument_key: malformed {key!r}")
    venue, market_type, contract = key.split(":", 2)
    if (venue, market_type, contract) != (
        raw["venue"], raw["market_type"], raw["contract_code"]
    ):
        raise RegistryError(
            f"instruments[{index}]: key {key!r} contradicts venue/market_type/contract_code"
        )
    if raw["status"] not in {"active", "deprecated"}:
        raise RegistryError(f"instruments[{index}].status: unknown {raw['status']!r}")
    periods = raw.get("supported_periods")
    if not isinstance(periods, list) or not periods:
        raise RegistryError(f"instruments[{index}].supported_periods: expected a non-empty list")
    unknown = [p for p in periods if p not in SUPPORTED_PERIODS]
    if unknown:
        raise RegistryError(
            f"instruments[{index}].supported_periods: unsupported {unknown}"
        )
    return InstrumentSpec(
        instrument_key=key,
        venue=raw["venue"],
        market_type=raw["market_type"],
        contract_code=raw["contract_code"],
        price_tick=_positive_decimal(raw, "price_tick"),
        quantity_step=_positive_decimal(raw, "quantity_step"),
        contract_size=_positive_decimal(raw, "contract_size"),
        settlement_currency=raw["settlement_currency"],
        fee_schedule_version=raw["fee_schedule_version"],
        status=raw["status"],
        supported_periods=tuple(str(p) for p in periods),
    )


class InstrumentRegistry:
    def __init__(self, specs: list[InstrumentSpec]) -> None:
        self._by_key: dict[str, InstrumentSpec] = {}
        for spec in specs:
            if spec.instrument_key in self._by_key:
                raise RegistryError(f"duplicate instrument_key {spec.instrument_key!r}")
            self._by_key[spec.instrument_key] = spec
        if not specs:
            raise RegistryError("registry: at least one instrument is required")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "InstrumentRegistry":
        if int(payload.get("schema_version", 0)) != SCHEMA_VERSION:
            raise RegistryError(f"schema_version: expected {SCHEMA_VERSION}")
        raw = payload.get("instruments")
        if not isinstance(raw, list):
            raise RegistryError("instruments: expected a list")
        return cls([_parse_spec(item, i) for i, item in enumerate(raw)])

    @classmethod
    def from_path(cls, path: str | Path) -> "InstrumentRegistry":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise RegistryError(f"registry file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry file is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise RegistryError("registry: expected a JSON object")
        return cls.from_payload(payload)

    def get(self, instrument_key: str) -> InstrumentSpec:
        try:
            return self._by_key[instrument_key]
        except KeyError:
            raise InstrumentNotFound(instrument_key) from None

    def list(self) -> list[InstrumentSpec]:
        return list(self._by_key.values())


def default_registry_path() -> Path:
    override = os.getenv("WORED_INSTRUMENT_REGISTRY", "").strip()
    if override:
        return Path(override)
    container = Path("/config/instrument_registry.json")
    if container.exists():
        return container
    return Path(__file__).resolve().parent.parent / "config" / "instrument_registry.json"


_registry_cache: InstrumentRegistry | None = None


def load_registry(*, force_reload: bool = False) -> InstrumentRegistry:
    """Load and cache the registry; call with ``force_reload`` in tests."""
    global _registry_cache
    if _registry_cache is None or force_reload:
        _registry_cache = InstrumentRegistry.from_path(default_registry_path())
    return _registry_cache
