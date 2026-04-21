"""Handoff builder — create context handoff packages for model switches with versioning."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict

from core.schemas import HandoffPackage
from context.service import ContextService

log = logging.getLogger(__name__)


class HandoffSchemaV1:
    """Schema definition for handoff package v1."""

    SCHEMA_VERSION = "v1"
    
    @staticmethod
    def validate(handoff: Dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate handoff package against v1 schema.
        
        Returns:
            (is_valid, errors) tuple
        """
        errors: list[str] = []
        required_fields = ["version", "system_rules", "handoff_summary", "last_user_request"]
        
        for field in required_fields:
            if field not in handoff:
                errors.append(f"Missing required field: {field}")
            elif not isinstance(handoff[field], str):
                errors.append(f"Field '{field}' must be a string")
            elif len(handoff[field]) == 0:
                errors.append(f"Field '{field}' cannot be empty")
        
        # Validate size limits
        max_field_size = 50000
        for field in ["system_rules", "handoff_summary", "last_user_request"]:
            if field in handoff and len(handoff[field]) > max_field_size:
                errors.append(f"Field '{field}' exceeds max size of {max_field_size} chars")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def serialize(handoff: HandoffPackage) -> str:
        """Serialize handoff to JSON string."""
        return json.dumps(asdict(handoff), ensure_ascii=False, indent=2)
    
    @staticmethod
    def deserialize(data: str) -> HandoffPackage:
        """Deserialize JSON string to HandoffPackage."""
        obj = json.loads(data)
        is_valid, errors = HandoffSchemaV1.validate(obj)
        if not is_valid:
            raise ValueError(f"Invalid handoff schema: {'; '.join(errors)}")
        
        return HandoffPackage(
            version=obj["version"],
            system_rules=obj["system_rules"],
            handoff_summary=obj["handoff_summary"],
            last_user_request=obj["last_user_request"],
            delta_market_update=obj.get("delta_market_update"),
            created_at=datetime.fromisoformat(obj["created_at"]) if isinstance(obj.get("created_at"), str) else datetime.utcnow(),
        )


class HandoffBuilder:
    """Builds context handoff packages for model switches with schema validation."""

    def __init__(self, context_service: ContextService) -> None:
        self._ctx = context_service
        self._schema = HandoffSchemaV1()

    async def build_handoff(
        self,
        old_session_id: str,
        new_model: str,
        old_model: str,
        market_facts: str | None = None,
        include_token_state: bool = True,
    ) -> HandoffPackage:
        """Build a handoff package for switching models.
        
        Args:
            old_session_id: Source session ID
            new_model: Target model ID
            old_model: Current model ID
            market_facts: Optional market data snapshot
            include_token_state: Include token budget state in summary
        
        Returns:
            Validated HandoffPackage
        """
        msgs = await self._ctx.get_conversation_history(old_session_id, max_tokens=2000)
        summary = self._build_summary(msgs, include_token_state=include_token_state)
        last_user_request = self._last_user_message(msgs)
        
        system_rules = self._build_system_rules(old_model, new_model)
        
        handoff = HandoffPackage(
            version="v1",
            system_rules=system_rules,
            handoff_summary=summary,
            last_user_request=last_user_request,
            delta_market_update=market_facts,
            created_at=datetime.utcnow(),
        )
        
        # Validate before returning
        obj_dict = asdict(handoff)
        is_valid, errors = self._schema.validate(obj_dict)
        if not is_valid:
            log.error("Handoff validation failed: %s", errors)
            raise ValueError(f"Handoff package validation failed: {'; '.join(errors)}")
        
        log.info("Built handoff package v1 from %s to %s", old_model, new_model)
        return handoff

    async def apply_handoff(self, session_id: str, handoff: HandoffPackage) -> int:
        """Apply handoff package to a new session.
        
        Args:
            session_id: Target session ID
            handoff: Handoff package to apply
        
        Returns:
            Number of messages added
        """
        msg_count = 0
        
        # Add system message with full handoff context
        system_msg = self._format_system_message(handoff)
        await self._ctx.add_message(session_id, "system", system_msg)
        msg_count += 1
        
        # Optionally replay last user message as context
        if handoff.last_user_request:
            await self._ctx.add_message(session_id, "user", f"[Continuing from previous session]\n{handoff.last_user_request}")
            msg_count += 1
        
        log.info("Applied handoff to session %s: %d messages added", session_id, msg_count)
        return msg_count

    def serialize(self, handoff: HandoffPackage) -> str:
        """Serialize handoff to JSON."""
        return self._schema.serialize(handoff)

    def deserialize(self, data: str) -> HandoffPackage:
        """Deserialize JSON to handoff."""
        return self._schema.deserialize(data)

    def _build_system_rules(self, old_model: str, new_model: str) -> str:
        """Build system prompt for handoff."""
        return (
            "You are continuing an AI analysis session that was started with a different model.\n\n"
            f"Previous model: {old_model}\n"
            f"Current model: {new_model}\n\n"
            "Your responsibilities:\n"
            "1. Preserve all context, conclusions, and user intent from the previous session\n"
            "2. Do not repeat information already provided\n"
            "3. Continue the analysis seamlessly as if you were the same assistant\n"
            "4. Maintain consistency in tone, style, and formatting\n"
            "5. If you detect any gaps or ambiguities, ask clarifying questions\n\n"
            "Important: The user expects continuity. Do not restart the conversation."
        )

    def _build_summary(self, messages: list[dict], include_token_state: bool = True) -> str:
        """Build compact summary of conversation history."""
        if not messages:
            return "No prior context available."

        user_intents: list[str] = []
        model_conclusions: list[str] = []
        key_topics: set[str] = set()

        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            
            if role == "user":
                user_intents.append(content[:200])
            elif role == "assistant":
                model_conclusions.append(content[:300])

        parts: list[str] = []
        
        if user_intents:
            parts.append("**User Requests:**\n- " + "\n- ".join(user_intents[-5:]))
        
        if model_conclusions:
            parts.append("**Model Conclusions:**\n- " + "\n- ".join(model_conclusions[-5:]))

        if include_token_state:
            total_msgs = len(messages)
            parts.append(f"**Session Stats:** {total_msgs} messages in history")

        return "\n\n".join(parts) if parts else "No substantive context available."

    def _last_user_message(self, messages: list[dict]) -> str:
        """Extract last user message."""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                return content[:800]
        return ""

    def _format_system_message(self, handoff: HandoffPackage) -> str:
        """Format handoff as system message."""
        lines = [
            "=" * 60,
            "CONTEXT HANDOFF PACKAGE",
            f"Version: {handoff.version}",
            f"Created: {handoff.created_at.isoformat()}",
            "=" * 60,
            "",
            handoff.system_rules,
            "",
            "--- HANDOFF SUMMARY ---",
            handoff.handoff_summary,
            "",
        ]
        
        if handoff.last_user_request:
            lines.extend([
                "--- LAST USER REQUEST ---",
                handoff.last_user_request,
                "",
            ])
        
        if handoff.delta_market_update:
            lines.extend([
                "--- MARKET UPDATE ---",
                handoff.delta_market_update,
                "",
            ])
        
        lines.append("=" * 60)
        return "\n".join(lines)
