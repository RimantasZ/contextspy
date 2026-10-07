# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Where, and how often, one block's content occurs across the requests of a scope.

Pure aggregation over rows the DB layer loads (see ``db/block_occurrence_service.py``). A *scope* is
an ordered list of requests (a whole session, one conversation, or a single request). Presence is
reported as runs of consecutive positions in that order, so a block that is in every request of a
conversation is one run even when other conversations' requests are interleaved in session order.
Token figures are visible-block tokens, never provider totals. See plans/archive/info-panel.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

SAMPLE_LIMIT = 50


@dataclass(frozen=True)
class ScopeRequest:
    request_id: str
    session_seq: int | None
    conversation_code: str | None
    context_fidelity: str


@dataclass(frozen=True)
class OccurrenceRow:
    """One stored block row with the selected block's content hash."""

    request_id: str
    block_id: int
    token_count: int


class OccurrenceIndex:
    def __init__(
        self,
        scope: Sequence[ScopeRequest],
        rows: Sequence[OccurrenceRow],
        *,
        current_request_id: str,
        current_block_id: int,
    ) -> None:
        self.scope = list(scope)
        self.current_request_id = current_request_id
        self._position_of = {request.request_id: index for index, request in enumerate(self.scope)}
        # position -> [block_id (the current block when it is in that request, else the lowest id), tokens, rows]
        self._by_position: dict[int, list] = {}
        self._token_counts: set[int] = set()
        for row in sorted(rows, key=lambda r: r.block_id):
            position = self._position_of.get(row.request_id)
            if position is None:
                continue  # not in this scope
            entry = self._by_position.setdefault(position, [row.block_id, 0, 0, row.token_count])
            if row.block_id == current_block_id:
                entry[0], entry[3] = row.block_id, row.token_count
            entry[1] += row.token_count
            entry[2] += 1
            self._token_counts.add(row.token_count)
        self.positions = sorted(self._by_position)

    # -- runs ---------------------------------------------------------------

    def runs(self) -> list[dict]:
        runs: list[dict] = []
        start = previous = None
        for position in self.positions:
            if start is None:
                start = previous = position
            elif position == previous + 1:
                previous = position
            else:
                runs.append(self._run(start, previous))
                start = previous = position
        if start is not None:
            runs.append(self._run(start, previous))
        return runs

    def _run(self, start: int, end: int) -> dict:
        members = [self._by_position[p] for p in range(start, end + 1)]
        return {
            "from_position": start,
            "to_position": end,
            "from_seq": self.scope[start].session_seq,
            "to_seq": self.scope[end].session_seq,
            "request_count": len(members),
            "occurrence_count": sum(member[2] for member in members),
        }

    # -- entries ------------------------------------------------------------

    def entry(self, position: int) -> dict:
        block_id, _tokens, _count, token_count = self._by_position[position]
        request = self.scope[position]
        return {
            "request_id": request.request_id,
            "block_id": block_id,
            "position": position,
            "session_seq": request.session_seq,
            "conversation_code": request.conversation_code,
            "token_count": token_count,
            "context_fidelity": request.context_fidelity,
            "is_current": request.request_id == self.current_request_id,
        }

    def sample(self) -> list[dict]:
        """First, last and current occurrences plus both ends of every run, capped at SAMPLE_LIMIT."""
        wanted: list[int] = []
        if self.positions:
            wanted += [self.positions[0], self.positions[-1]]
        current = self._position_of.get(self.current_request_id)
        if current in self._by_position:
            wanted.append(current)
        for run in self.runs():
            wanted += [run["from_position"], run["to_position"]]
        unique: list[int] = []
        for position in wanted:  # keep priority order, drop repeats
            if position not in unique:
                unique.append(position)
        return [self.entry(position) for position in sorted(unique[:SAMPLE_LIMIT])]

    def entries(self, from_position: int, to_position: int, limit: int) -> tuple[list[dict], bool]:
        """Occurring requests with positions in [from_position, to_position], plus whether more remain."""
        in_range = [p for p in self.positions if from_position <= p <= to_position]
        return [self.entry(p) for p in in_range[:limit]], len(in_range) > limit

    # -- totals -------------------------------------------------------------

    def totals(self) -> dict:
        present = [self._by_position[p] for p in self.positions]
        fidelity: dict[str, int] = {}
        for position in self.positions:
            key = self.scope[position].context_fidelity
            fidelity[key] = fidelity.get(key, 0) + 1
        first = self.scope[self.positions[0]] if self.positions else None
        last = self.scope[self.positions[-1]] if self.positions else None
        return {
            "occurrence_count": sum(member[2] for member in present),
            "request_count": len(present),
            # One figure only when every occurrence agrees (system prompts counted with a per-request header can differ).
            "tokens_per_occurrence": next(iter(self._token_counts)) if len(self._token_counts) == 1 else None,
            "total_visible_tokens": sum(member[1] for member in present),
            "first_seen_session_seq": first.session_seq if first else None,
            "last_seen_session_seq": last.session_seq if last else None,
            "in_latest_request_of_scope": bool(self.scope) and (len(self.scope) - 1) in self._by_position,
            "scope_request_count": len(self.scope),
            "fidelity_counts": fidelity,
        }
