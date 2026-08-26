# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Transport-neutral application-payload capture types and decoders.

Transport decoders preserve event ordering and payload fields but deliberately do
not understand provider semantics. Wire-format adapters reduce these events into
the provider's canonical buffered response JSON before block analysis.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapturedEvent:
    sequence: int
    direction: str = "server_to_client"
    kind: str = "json"
    payload: Any = None
    text: str | None = None
    event: str | None = None
    event_id: str | None = None
    retry_ms: int | None = None
    comments: list[str] | None = None
    done: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sequence": self.sequence,
            "direction": self.direction,
            "kind": self.kind,
        }
        if self.payload is not None:
            result["payload"] = self.payload
        if self.text is not None:
            result["text"] = self.text
        if self.event is not None:
            result["event"] = self.event
        if self.event_id is not None:
            result["event_id"] = self.event_id
        if self.retry_ms is not None:
            result["retry_ms"] = self.retry_ms
        if self.comments:
            result["comments"] = self.comments
        if self.done:
            result["done"] = True
        return result


@dataclass
class CanonicalResponse:
    payload: dict[str, Any]
    transport: str
    events: list[CapturedEvent] = field(default_factory=list)
    reconstructed: bool = False
    complete: bool = True
    error: dict[str, Any] | None = None

    def events_json(self) -> str | None:
        if not self.events:
            return None
        return json.dumps([event.to_dict() for event in self.events], ensure_ascii=False)


def decode_sse(raw: bytes) -> list[CapturedEvent]:
    """Decode SSE records without applying provider-specific semantics.

    Supports record boundaries, multiline data, optional spaces after the colon,
    event/id/retry fields, JSON and non-JSON data, and the conventional [DONE]
    sentinel. Nothing with application payload is silently dropped.
    """
    text = raw.decode("utf-8", errors="replace")
    records: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line == "":
            if current:
                records.append(current)
                current = []
        else:
            current.append(line)
    if current:
        records.append(current)

    # A number of OpenAI-compatible servers emit one complete JSON `data:`
    # payload per line without the blank record separator required by SSE.
    # Treat that widespread shape as discrete events when every line is itself
    # valid JSON (or [DONE]); retain true multiline data as one record.
    expanded_records: list[list[str]] = []
    for record in records:
        data_only = [line for line in record if line.startswith("data:")]
        other_fields = [line for line in record if not line.startswith("data:")]
        individually_complete = len(data_only) > 1 and not other_fields
        if individually_complete:
            for line in data_only:
                value = line.partition(":")[2].lstrip()
                if value != "[DONE]":
                    try:
                        json.loads(value)
                    except json.JSONDecodeError:
                        individually_complete = False
                        break
        if individually_complete:
            expanded_records.extend([line] for line in data_only)
        else:
            expanded_records.append(record)
    records = expanded_records

    events: list[CapturedEvent] = []
    for record in records:
        event_name: str | None = None
        event_id: str | None = None
        retry_ms: int | None = None
        data_lines: list[str] = []
        comments: list[str] = []
        for line in record:
            if line.startswith(":"):
                comments.append(line[1:].lstrip())
                continue
            field, sep, value = line.partition(":")
            if sep and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data_lines.append(value if sep else "")
            elif field == "event":
                event_name = value
            elif field == "id":
                event_id = value
            elif field == "retry":
                try:
                    retry_ms = int(value)
                except ValueError:
                    pass

        data_text = "\n".join(data_lines)
        # Pure keep-alive comments have no application payload. Preserve a
        # comment only when it shares a record with observable SSE fields.
        if not data_lines and event_name is None and event_id is None and retry_ms is None:
            continue
        done = data_text.strip() == "[DONE]"
        payload: Any = None
        kind = "text"
        if data_text and not done:
            try:
                payload = json.loads(data_text)
                kind = "json"
            except json.JSONDecodeError:
                payload = data_text
        event = CapturedEvent(
            sequence=len(events),
            kind=kind,
            payload=payload,
            text=data_text if kind != "json" and data_text else None,
            event=event_name,
            event_id=event_id,
            retry_ms=retry_ms,
            comments=comments or None,
            done=done,
        )
        events.append(event)
    return events


def decode_ndjson(raw: bytes) -> list[CapturedEvent]:
    """Decode newline-delimited JSON while preserving malformed text records."""
    events: list[CapturedEvent] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
            kind = "json"
            raw_text = None
        except json.JSONDecodeError:
            payload = line
            kind = "text"
            raw_text = line
        events.append(CapturedEvent(
            sequence=len(events), kind=kind, payload=payload, text=raw_text,
        ))
    return events
