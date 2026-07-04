# Copyright 2026 Rimantas Zukaitis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import re
from dataclasses import dataclass

from contextspy.analysis.blocks import AnalyzedRequest, Block, BlockType

# ---------------------------------------------------------------------------
# File content heuristics
# ---------------------------------------------------------------------------

_XML_FILE_TAGS = re.compile(
    r"<(?:file_contents|file|source|document_content)[\s>]", re.IGNORECASE
)
_FENCE_WITH_FILENAME = re.compile(
    r"^(?:```|~~~)\w*\s+\S+\.\w{1,10}\s*$", re.MULTILINE
)
_FENCE_BLOCK = re.compile(r"(?:```|~~~)[^\n]*\n(.*?)(?:```|~~~)", re.DOTALL)
_COMMENT_PATH = re.compile(
    r"^(?://|#)\s+\S+[/\\]\S+\.\w{2,10}\s*$", re.MULTILINE
)
_PATH_THEN_FENCE = re.compile(
    r"^[^\n]+[/\\][^\n]+\.\w{2,10}\s*\n(?:```|~~~)", re.MULTILINE
)


def _is_file_content(text: str) -> bool:
    if _XML_FILE_TAGS.search(text):
        return True
    if _FENCE_WITH_FILENAME.search(text):
        return True
    # Large fenced code block (≥50 lines)
    for match in _FENCE_BLOCK.finditer(text):
        if match.group(1).count("\n") >= 50:
            return True
    if _COMMENT_PATH.search(text):
        return True
    if _PATH_THEN_FENCE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Category breakdown dataclass
# ---------------------------------------------------------------------------

@dataclass
class CategoryBreakdown:
    system_prompt: int = 0
    tool_definitions: int = 0
    tool_results: int = 0
    file_contents: int = 0
    conversation_history: int = 0
    current_user_message: int = 0
    assistant_prefill: int = 0
    uncategorized: int = 0
    total_input: int = 0
    tokens_output_text: int = 0
    tokens_output_thinking: int = 0
    total_output: int = 0

    def to_db_fields(self) -> dict:
        return {
            "tokens_system_prompt": self.system_prompt,
            "tokens_tool_definitions": self.tool_definitions,
            "tokens_tool_results": self.tool_results,
            "tokens_file_contents": self.file_contents,
            "tokens_conversation_history": self.conversation_history,
            "tokens_current_user_message": self.current_user_message,
            "tokens_assistant_prefill": self.assistant_prefill,
            "tokens_uncategorized": self.uncategorized,
            "tokens_total_input": self.total_input,
            "tokens_total_output": self.total_output,
            "tokens_output_text": self.tokens_output_text,
            "tokens_output_thinking": self.tokens_output_thinking,
        }


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_blocks(input_blocks: list[Block]) -> None:
    """Assign each input block's `category` in place.

    Priority: tool_results > system_prompt > assistant_prefill > file_contents >
              current_user_message > conversation_history > tool_definitions > uncategorized
    """
    last_user_idx: int | None = None
    for b in input_blocks:
        if b.block_type == BlockType.USER_MESSAGE and not b.attrs.get("is_prefill"):
            if b.message_index is not None:
                last_user_idx = b.message_index

    for b in input_blocks:
        if b.block_type == BlockType.TOOL_RESULT:
            b.category = "tool_results"
        elif b.block_type == BlockType.SYSTEM_PROMPT:
            b.category = "system_prompt"
        elif b.attrs.get("is_prefill"):
            b.category = "assistant_prefill"
        elif b.block_type == BlockType.USER_MESSAGE:
            if _is_file_content(b.content):
                b.category = "file_contents"
            elif b.message_index == last_user_idx:
                b.category = "current_user_message"
            else:
                b.category = "conversation_history"
        elif b.block_type == BlockType.TOOL_DEFINITION:
            b.category = "tool_definitions"
        elif b.block_type in (BlockType.ASSISTANT_MESSAGE, BlockType.TOOL_CALL, BlockType.THINKING, BlockType.OTHER):
            b.category = "file_contents" if _is_file_content(b.content) else "conversation_history"
        else:
            b.category = "uncategorized"


def classify(analyzed: AnalyzedRequest) -> CategoryBreakdown:
    classify_blocks(analyzed.input_blocks)

    breakdown = CategoryBreakdown()
    for b in analyzed.input_blocks:
        cat = b.category or "uncategorized"
        setattr(breakdown, cat, getattr(breakdown, cat) + b.token_count)

    # ChatML overhead: ~4 tokens per message (role marker + framing tokens) +
    # 3 tokens to prime the reply.  This matches OpenAI's own token-counting
    # formula and significantly reduces the systematic undercount for long
    # multi-turn / tool-heavy conversations. Tool-definition blocks carry no
    # message_index so they don't inflate the message count.
    distinct_message_indices = {
        b.message_index for b in analyzed.input_blocks if b.message_index is not None
    }
    chatml_overhead = 4 * len(distinct_message_indices) + 3

    breakdown.total_input = (
        breakdown.system_prompt
        + breakdown.tool_definitions
        + breakdown.tool_results
        + breakdown.file_contents
        + breakdown.conversation_history
        + breakdown.current_user_message
        + breakdown.assistant_prefill
        + breakdown.uncategorized
        + chatml_overhead
    )

    for b in analyzed.output_blocks:
        if b.block_type == BlockType.THINKING:
            breakdown.tokens_output_thinking += b.token_count
        else:
            breakdown.tokens_output_text += b.token_count
    breakdown.total_output = breakdown.tokens_output_text + breakdown.tokens_output_thinking

    return breakdown


def per_tool_tokens(analyzed: AnalyzedRequest) -> list[dict]:
    """Return per-tool definition token counts and result token counts.

    Definition tokens: each tool definition block counted individually.
    Result tokens: attributed via the tool_result block's tool_name (already
    resolved by the adapter from tool_call_map); falls back to even
    distribution only when the tool name cannot be resolved.
    """
    def_blocks = [b for b in analyzed.input_blocks if b.block_type == BlockType.TOOL_DEFINITION]
    if not def_blocks:
        return []

    rows: list[dict] = []
    name_to_idx: dict[str, int] = {}
    for b in def_blocks:
        name = b.tool_name or "unknown"
        name_to_idx[name] = len(rows)
        rows.append({"tool_name": name, "definition_tokens": b.token_count, "result_tokens": 0})

    unattributed_tokens = 0
    for b in analyzed.input_blocks:
        if b.block_type != BlockType.TOOL_RESULT or b.token_count == 0:
            continue
        if b.tool_name and b.tool_name in name_to_idx:
            rows[name_to_idx[b.tool_name]]["result_tokens"] += b.token_count
        else:
            unattributed_tokens += b.token_count

    if unattributed_tokens > 0 and rows:
        per = unattributed_tokens // len(rows)
        remainder = unattributed_tokens % len(rows)
        for i, row in enumerate(rows):
            row["result_tokens"] += per + (1 if i < remainder else 0)

    return rows
