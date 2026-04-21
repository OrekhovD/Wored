"""Tests for context handoff."""
import pytest
from datetime import datetime

from core.schemas import HandoffPackage
from context.handoff import HandoffBuilder, HandoffSchemaV1


@pytest.mark.asyncio
async def test_handoff_schema_v1_serialization():
    """HandoffSchemaV1 serializes correctly."""
    handoff = HandoffPackage(
        version="v1",
        system_rules="System rules",
        handoff_summary="Summary",
        last_user_request="Last request",
        delta_market_update="Market update",
        created_at=datetime.utcnow(),
    )
    
    serialized = HandoffSchemaV1.serialize(handoff)
    assert isinstance(serialized, str)
    
    deserialized = HandoffSchemaV1.deserialize(serialized)
    assert deserialized.version == handoff.version
    assert deserialized.system_rules == handoff.system_rules
    assert deserialized.handoff_summary == handoff.handoff_summary
    assert deserialized.last_user_request == handoff.last_user_request
    assert deserialized.delta_market_update == handoff.delta_market_update


@pytest.mark.asyncio
async def test_handoff_schema_v1_validation():
    """HandoffSchemaV1 validates correctly."""
    valid_data = {
        "version": "v1",
        "system_rules": "Test rules",
        "handoff_summary": "Test summary",
        "last_user_request": "Test request",
        "delta_market_update": "Market update",
        "created_at": datetime.utcnow().isoformat(),
    }
    
    is_valid, errors = HandoffSchemaV1.validate(valid_data)
    assert is_valid
    assert len(errors) == 0
    
    invalid_data = {
        "version": "v1",
        "handoff_summary": "",  # empty
        "last_user_request": "",  # empty
        "created_at": datetime.utcnow().isoformat(),
    }
    is_valid, errors = HandoffSchemaV1.validate(invalid_data)
    assert not is_valid
    assert any("Missing required field" in error for error in errors)
    assert any("cannot be empty" in error for error in errors)


@pytest.mark.asyncio
async def test_handoff_builder_summary():
    """HandoffBuilder builds summary from messages."""
    messages = [
        {"role": "user", "content": "Анализ BTC/USDT"},
        {"role": "assistant", "content": "BTC показывает восходящий тренд"},
        {"role": "user", "content": "Какие показатели?"},
        {"role": "assistant", "content": "Volume +5%, RSI = 67"},
    ]
    
    builder = HandoffBuilder(None)
    summary = builder._build_summary(messages)
    
    assert "User Requests" in summary
    assert "Model Conclusions" in summary
    assert "BTC" in summary
    assert "Volume" in summary


@pytest.mark.asyncio
async def test_handoff_builder_empty_summary():
    """HandoffBuilder handles empty message list."""
    builder = HandoffBuilder(None)
    summary = builder._build_summary([])
    assert summary == "No prior context available."


@pytest.mark.asyncio
async def test_handoff_builder_last_user_message():
    """HandoffBuilder extracts last user message."""
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "Response"},
        {"role": "user", "content": "Last user message"},
        {"role": "assistant", "content": "Final response"},
    ]
    
    builder = HandoffBuilder(None)
    last_message = builder._last_user_message(messages)
    assert last_message == "Last user message"
    
    # Test empty list
    last_message = builder._last_user_message([])
    assert last_message == ""


@pytest.mark.asyncio
async def test_handoff_builder_format_system_message():
    """HandoffBuilder formats system message correctly."""
    builder = HandoffBuilder(None)
    handoff = HandoffPackage(
        version="v1",
        system_rules="System rules",
        handoff_summary="Test summary",
        last_user_request="Last request",
        delta_market_update="Market update",
        created_at=datetime.utcnow(),
    )
    
    formatted = builder._format_system_message(handoff)
    
    assert "CONTEXT HANDOFF PACKAGE" in formatted
    assert "Version: v1" in formatted
    assert "System rules" in formatted
    assert "--- HANDOFF SUMMARY ---" in formatted
    assert "Test summary" in formatted
    assert "--- LAST USER REQUEST ---" in formatted
    assert "Last request" in formatted
    assert "--- MARKET UPDATE ---" in formatted
    assert "Market update" in formatted