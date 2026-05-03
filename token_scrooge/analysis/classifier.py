from __future__ import annotations

import re
from dataclasses import dataclass

from token_scrooge.analysis.providers import ParsedMessage, ParsedRequest
from token_scrooge.analysis.tokenizer import count_tokens

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
        }


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify(parsed: ParsedRequest) -> CategoryBreakdown:
    breakdown = CategoryBreakdown()

    # Tool definitions are from the top-level tools array, counted once
    breakdown.tool_definitions = count_tokens(parsed.tool_definitions_text)

    # Find the last user message index
    last_user_idx = -1
    for i, msg in enumerate(parsed.messages):
        if msg.role == "user" and not msg.is_tool_result:
            last_user_idx = i

    for i, msg in enumerate(parsed.messages):
        tokens = count_tokens(msg.content)

        # Priority: tool_results > system_prompt > assistant_prefill >
        #           file_contents > current_user_message > conversation_history > uncategorized

        if msg.is_tool_result:
            breakdown.tool_results += tokens
        elif msg.role == "system":
            breakdown.system_prompt += tokens
        elif msg.is_assistant_prefill:
            breakdown.assistant_prefill += tokens
        elif msg.role == "user":
            if i == last_user_idx:
                if _is_file_content(msg.content):
                    breakdown.file_contents += tokens
                else:
                    breakdown.current_user_message += tokens
            else:
                if _is_file_content(msg.content):
                    breakdown.file_contents += tokens
                else:
                    breakdown.conversation_history += tokens
        elif msg.role == "assistant":
            if _is_file_content(msg.content):
                breakdown.file_contents += tokens
            else:
                breakdown.conversation_history += tokens
        else:
            breakdown.uncategorized += tokens

    breakdown.total_input = (
        breakdown.system_prompt
        + breakdown.tool_definitions
        + breakdown.tool_results
        + breakdown.file_contents
        + breakdown.conversation_history
        + breakdown.current_user_message
        + breakdown.assistant_prefill
        + breakdown.uncategorized
    )
    breakdown.total_output = count_tokens(parsed.response_text)

    return breakdown


def per_tool_tokens(parsed: ParsedRequest) -> list[dict]:
    """Return per-tool definition token counts and result token counts.

    Definition tokens: each tool definition counted individually.
    Result tokens: attributed to the specific tool via tool_call_id mapping;
    falls back to even distribution only when the call_id cannot be resolved.
    """
    if not parsed.tool_definitions_text:
        return []

    try:
        import json
        tools: list[dict] = json.loads(parsed.tool_definitions_text)
    except Exception:
        return []

    if not tools:
        return []

    rows: list[dict] = []
    for tool in tools:
        name = tool.get("name") or tool.get("function", {}).get("name") or "unknown"
        def_tokens = count_tokens(json.dumps(tool))
        rows.append({"tool_name": name, "definition_tokens": def_tokens, "result_tokens": 0})

    name_to_idx = {row["tool_name"]: i for i, row in enumerate(rows)}

    unattributed_tokens = 0
    for msg in parsed.messages:
        if not msg.is_tool_result:
            continue
        tokens = count_tokens(msg.content)
        if tokens == 0:
            continue
        # Resolve via tool_call_id → tool_name map
        tool_name = parsed.tool_call_map.get(msg.tool_call_id or "") if msg.tool_call_id else None
        if tool_name and tool_name in name_to_idx:
            rows[name_to_idx[tool_name]]["result_tokens"] += tokens
        else:
            unattributed_tokens += tokens

    # Distribute any unattributed tokens evenly
    if unattributed_tokens > 0 and rows:
        per = unattributed_tokens // len(rows)
        remainder = unattributed_tokens % len(rows)
        for i, row in enumerate(rows):
            row["result_tokens"] += per + (1 if i < remainder else 0)

    return rows
