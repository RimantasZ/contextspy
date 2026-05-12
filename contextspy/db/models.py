from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    requests: Mapped[list["Request"]] = relationship(
        "Request", back_populates="session", passive_deletes=True
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "is_active": bool(self.is_active),
        }


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Token counts by category
    tokens_system_prompt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_tool_definitions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_tool_results: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_file_contents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_conversation_history: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_current_user_message: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_assistant_prefill: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_uncategorized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_total_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_total_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Provider-reported usage
    provider_input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider_output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Anthropic prompt-cache breakdown (None for non-Anthropic providers)
    cache_read_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    tokenizer: Mapped[str] = mapped_column(
        String, nullable=False, default="tiktoken/cl100k_base"
    )

    # Raw content (NULLed when session ends)
    raw_request_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    session: Mapped[Optional[Session]] = relationship("Session", back_populates="requests")

    def to_dict(self, include_raw: bool = True) -> dict:
        d = {
            "id": self.id,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "agent": self.agent,
            "endpoint": self.endpoint,
            "duration_ms": self.duration_ms,
            "status_code": self.status_code,
            "tokens_system_prompt": self.tokens_system_prompt,
            "tokens_tool_definitions": self.tokens_tool_definitions,
            "tokens_tool_results": self.tokens_tool_results,
            "tokens_file_contents": self.tokens_file_contents,
            "tokens_conversation_history": self.tokens_conversation_history,
            "tokens_current_user_message": self.tokens_current_user_message,
            "tokens_assistant_prefill": self.tokens_assistant_prefill,
            "tokens_uncategorized": self.tokens_uncategorized,
            "tokens_total_input": self.tokens_total_input,
            "tokens_total_output": self.tokens_total_output,
            "provider_input_tokens": self.provider_input_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "tokenizer": self.tokenizer,
        }
        if include_raw:
            d["raw_request_body"] = self.raw_request_body
            d["raw_response_body"] = self.raw_response_body
        return d


# Indexes (defined after model for clarity)
Index("idx_requests_session", Request.session_id)
Index("idx_requests_timestamp", Request.timestamp)
Index("idx_requests_provider", Request.provider)


class ToolStat(Base):
    """Per-tool token breakdown — one row per tool name per request."""

    __tablename__ = "tool_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        String, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    definition_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "definition_tokens": self.definition_tokens,
            "result_tokens": self.result_tokens,
        }


Index("idx_tool_stats_request", ToolStat.request_id)
Index("idx_tool_stats_name", ToolStat.tool_name)
