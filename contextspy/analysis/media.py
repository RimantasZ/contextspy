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
"""Images, files and other binary parts: identity markers and token estimates.

Binary payloads are transport encodings, never text shown to the model, so they are
never given to the text tokenizer. Each one is replaced by a short marker that is unique
per payload (``[image png 2000×1224 sha256:1a2b3c4d]``) and described in
``attrs["media"]``. Images with readable dimensions get an estimate from the rule of the
**model** (not the wire format, so gateways such as Copilot work). Documents, audio and
video are not estimated. Rules and sources: plans/104-image-token-estimates.md.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any

from contextspy.analysis.tokenizer import count_tokens

# ``method`` of one media item, and ``attrs["token_estimate"]`` of a block.
PUBLISHED = "published"      # the provider's / model's documented rule
ROUGH = "rough"              # no published rule for this model
UNAVAILABLE = "unavailable"  # not estimated (no bytes, unreadable, or not an image)

MEDIA_CONTENT_TYPES = frozenset({
    "audio",
    "computer_screenshot",
    "document",
    "file",
    "image",
    "image_url",
    "input_audio",
    "input_file",
    "input_image",
    "input_video",
    "output_audio",
    "output_image",
    "video",
})
_IMAGE_TYPES = frozenset({"computer_screenshot", "image", "image_url", "input_image", "output_image"})
_TEXT_TYPES = ("text", "output_text", "input_text")

_DATA_URL = re.compile(r"^data:([^;,]*)(?:;[^;,]*)*?;base64,", re.IGNORECASE)
_EMBEDDED_DATA_URL = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=_-]{256,}")
_BASE64 = re.compile(r"[A-Za-z0-9+/_-]+={0,2}")
_MIN_BARE_BASE64 = 1024
_HEADER_CHARS = 87_380  # base64 characters covering the first 64 KiB


# ---------------------------------------------------------------------------
# Image dimensions
# ---------------------------------------------------------------------------

def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG, JPEG, GIF or WebP header; None when unreadable."""
    try:
        size = _dimensions(data)
    except (struct.error, IndexError):
        return None
    return size if size and size[0] > 0 and size[1] > 0 else None


def _dimensions(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", data[6:10])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            return (int.from_bytes(data[24:27], "little") + 1,
                    int.from_bytes(data[27:30], "little") + 1)
        if chunk == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
        return None
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 <= len(data):
            if data[index] != 0xFF:
                return None
            marker = data[index + 1]
            if marker == 0xFF:  # fill byte
                index += 1
                continue
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # markers without a length
                index += 2
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return width, height
            index += 2 + struct.unpack(">H", data[index + 2:index + 4])[0]
    return None


def _decode(encoded: str) -> bytes:
    try:
        usable = encoded[: len(encoded) - len(encoded) % 4]
        return base64.b64decode(usable)
    except (binascii.Error, ValueError):
        return b""


def _dimensions_of_base64(encoded: str) -> tuple[int, int] | None:
    size = image_dimensions(_decode(encoded[:_HEADER_CHARS]))
    if size is None and len(encoded) > _HEADER_CHARS:  # e.g. a JPEG with large metadata first
        size = image_dimensions(_decode(encoded))
    return size


# ---------------------------------------------------------------------------
# Token rules
# ---------------------------------------------------------------------------

def estimate_image_tokens(
    model: str | None, width: int, height: int, detail: str | None = None,
) -> tuple[int, str]:
    """Return (tokens, method) for one input image of ``width`` x ``height`` pixels."""
    name = (model or "").lower()
    if "claude" in name:
        return _claude_tokens(name, width, height), PUBLISHED
    # Gateways prefix model names ("openai/gpt-5.5"); the family is in the last part.
    short = name.rsplit("/", 1)[-1]
    openai = _openai_tokens(short, width, height, (detail or "auto").lower())
    if openai is not None:
        return openai, PUBLISHED
    if re.search(r"gemma-?3(?!n)", short):
        return 256, PUBLISHED
    return math.ceil(width / 32) * math.ceil(height / 32), ROUGH


def _claude_is_high_resolution(name: str) -> bool:
    """"Claude 4.7 and later" get the high-resolution tier; unknown names are assumed new."""
    if re.search(r"claude-(?:instant|[123])(?!\d)", name):
        return False
    match = re.search(r"claude-[a-z]+-(\d+)(?:[-.](\d{1,2})(?!\d))?", name)
    if match is None:
        return True
    return (int(match[1]), int(match[2] or 0)) >= (4, 7)


def _claude_tokens(name: str, width: int, height: int) -> int:
    max_edge, max_tokens = (2576, 4784) if _claude_is_high_resolution(name) else (1568, 1568)

    def patches(w: int, h: int) -> int:
        return math.ceil(w / 28) * math.ceil(h / 28)

    def fits(w: int, h: int) -> bool:
        return (math.ceil(w / 28) * 28 <= max_edge and math.ceil(h / 28) * 28 <= max_edge
                and patches(w, h) <= max_tokens)

    if fits(width, height):
        return patches(width, height)
    long_edge, short_edge = max(width, height), min(width, height)

    def scaled_short(value: int) -> int:
        return max(round(value * short_edge / long_edge), 1)

    low, high = 1, long_edge
    while low + 1 < high:
        middle = (low + high) // 2
        if fits(middle, scaled_short(middle)):
            low = middle
        else:
            high = middle
    return patches(low, scaled_short(low))


# (model pattern, base tokens, tokens per 512px tile); first match wins.
_TILE_MODELS = (
    (r"gpt-4o-mini", 2833, 5667),
    (r"gpt-4o|gpt-4\.1(?!-(?:mini|nano))", 85, 170),
    (r"gpt-5(?:\.1)?(?!\.|\d|-(?:mini|nano))", 70, 140),
    (r"o1(?!\d)|o3(?!\d|-mini)", 75, 150),
)
# (model pattern, multiplier, {detail: (max dimension, patch budget or None)}, what "auto" means)
_SMALL = (512, None)
_STANDARD = {"low": (2048, 6144), "high": (2048, 6144)}
_PATCH_MODELS = (
    (r"gpt-(?:[6-9]|\d\d)", 1.2, {"low": _SMALL, "high": (65535, 2500), "original": (65535, None)}, "original"),
    (r"gpt-5\.6", 1.2, {"low": _SMALL, "high": (2048, 2500), "original": (65535, None)}, "original"),
    (r"gpt-5\.5", 1.2, {"low": _SMALL, "high": (2048, 2500), "original": (6000, 10000)}, "original"),
    (r"gpt-5\.4", 1.2, {"low": (2048, 6144), "high": (2048, 2500), "original": (6000, 10000)}, "high"),
    # 5.3 is not listed in the docs; assumed to size like 5.2.
    (r"gpt-5\.[23]", 1.2, _STANDARD, "high"),
    (r"gpt-5-mini", 1.2, _STANDARD, "high"),
    (r"gpt-5-nano", 1.5, _STANDARD, "high"),
    (r"gpt-4\.1-mini", 1.62, _STANDARD, "high"),
    (r"gpt-4\.1-nano", 2.46, _STANDARD, "high"),
    (r"o4-mini", 1.72, _STANDARD, "high"),
)


def _fit(width: int, height: int, max_dimension: int) -> tuple[float, float]:
    scale = min(1.0, max_dimension / max(width, height))
    return width * scale, height * scale


def _openai_tokens(name: str, width: int, height: int, detail: str) -> int | None:
    for pattern, multiplier, levels, auto in _PATCH_MODELS:
        if not re.match(pattern, name):
            continue
        max_dimension, budget = levels.get(detail) or levels[auto if auto in levels else "high"]
        w, h = _fit(width, height, max_dimension)
        w, h = max(int(w), 1), max(int(h), 1)
        if budget is not None and math.ceil(w / 32) * math.ceil(h / 32) > budget:
            shrink = math.sqrt(32 * 32 * budget / (w * h))
            across, down = w * shrink / 32, h * shrink / 32
            shrink *= min(max(math.floor(across), 1) / across, max(math.floor(down), 1) / down)
            # The epsilon keeps an exact patch boundary from rounding down to one pixel less.
            w, h = max(int(w * shrink + 1e-6), 1), max(int(h * shrink + 1e-6), 1)
        patches = math.ceil(w / 32) * math.ceil(h / 32)
        return math.ceil(round(patches * multiplier, 6))
    for pattern, base, per_tile in _TILE_MODELS:
        if not re.match(pattern, name):
            continue
        if detail == "low":
            return base
        w, h = _fit(width, height, 2048)
        scale = min(1.0, 768 / min(w, h))
        w, h = int(w * scale + 1e-6), int(h * scale + 1e-6)
        return base + per_tile * math.ceil(w / 512) * math.ceil(h / 512)
    return None


# ---------------------------------------------------------------------------
# Media items
# ---------------------------------------------------------------------------

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _size_label(size: int) -> str:
    return f"{size / 1024:.0f} KB" if size >= 1024 else f"{size} B"


def _split_data_url(value: str) -> tuple[str | None, str | None]:
    """(mime, base64 payload) for a data URL, else (None, None)."""
    match = _DATA_URL.match(value)
    return (match[1] or None, value[match.end():]) if match else (None, None)


def _locate(part: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """(base64 data, mime, remote reference, file name) of any provider's media part."""
    data = mime = reference = name = None
    containers: list[Any] = [part]
    for key in ("source", "file", "input_audio", "image_url"):
        value = part.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        mime = mime or container.get("media_type") or container.get("mimeType") or container.get("format")
        name = name or container.get("filename") or container.get("title")
        for key in ("data", "file_data", "image_url", "url", "file_url", "file_id", "result"):
            value = container.get(key)
            if not isinstance(value, str) or not value:
                continue
            url_mime, payload = _split_data_url(value)
            if payload is not None:
                data, mime = data or payload, url_mime or mime
            elif key in ("data", "file_data", "result"):
                data = data or value
            else:
                reference = reference or value
    return data, mime, reference, name


def describe_media(part: dict, model: str | None, *, kind: str | None = None) -> tuple[str, dict]:
    """Marker text and ``attrs["media"]`` entry for one media part."""
    ptype = str(part.get("type") or "")
    kind = kind or ("image" if ptype in _IMAGE_TYPES else
                    "audio" if "audio" in ptype else
                    "video" if "video" in ptype else
                    "document" if ptype == "document" else "file")
    data, mime, reference, name = _locate(part)
    item: dict[str, Any] = {"kind": kind, "tokens": 0, "method": UNAVAILABLE}
    label = [kind]
    subtype = str(mime).rsplit("/", 1)[-1] if mime else None
    if mime:
        item["mime"] = mime
        label.append(subtype)
    if name:
        item["name"] = str(name)[:200]
        label.append(f"name={item['name']}")

    nested_image_url = part.get("image_url")
    detail = part.get("detail") or (
        nested_image_url.get("detail") if isinstance(nested_image_url, dict) else None
    )
    if detail:
        item["detail"] = detail

    if data:
        item["bytes"] = len(data) * 3 // 4 - data[-2:].count("=")
        item["digest"] = _digest(data)
        dimensions = _dimensions_of_base64(data) if kind == "image" else None
        if dimensions:
            item["width"], item["height"] = dimensions
            item["tokens"], item["method"] = estimate_image_tokens(model, *dimensions, detail)
            label.append(f"{dimensions[0]}×{dimensions[1]}")
        else:
            label.append(_size_label(item["bytes"]))
    else:
        item["digest"] = _digest(reference or json.dumps(part, sort_keys=True, default=str))
        label.append("by reference" if reference else "no data")
    label.append(f"sha256:{item['digest'][:8]}")
    return f"[{' '.join(str(value) for value in label)}]", item


# ---------------------------------------------------------------------------
# Rendering provider content values
# ---------------------------------------------------------------------------

def scrub_binary(value: Any) -> tuple[Any, bool]:
    """Replace encoded binary payloads inside an arbitrary JSON value; True when any was found.

    ``encrypted_content`` is left alone: it stands for text the model does read.
    """
    if isinstance(value, str):
        if len(value) >= _MIN_BARE_BASE64 and _BASE64.fullmatch(value):
            return f"[binary {_size_label(len(value) * 3 // 4)} sha256:{_digest(value)[:8]}]", True
        scrubbed, count = _EMBEDDED_DATA_URL.subn(
            lambda m: f"[binary {_size_label(len(m[0]) * 3 // 4)} sha256:{_digest(m[0])[:8]}]", value,
        )
        return (scrubbed, True) if count else (value, False)
    if isinstance(value, dict):
        found = False
        result = {}
        for key, child in value.items():
            if key == "encrypted_content":
                result[key] = child
                continue
            result[key], changed = scrub_binary(child)
            found = found or changed
        return result, found
    if isinstance(value, list):
        pairs = [scrub_binary(child) for child in value]
        return [child for child, _ in pairs], any(changed for _, changed in pairs)
    return value, False


@dataclass
class Rendered:
    """A content value turned into block text."""

    text: str                    # what is stored: text plus one marker per media item
    plain: str                   # what the text tokenizer may count: no markers
    media: list[dict] = field(default_factory=list)
    document_text: bool = False  # holds the text of a text document
    scrubbed: bool = False       # an unrecognised payload was removed

    @property
    def token_count(self) -> int:
        return count_tokens(self.plain) + sum(item["tokens"] for item in self.media)

    def attrs(self) -> dict:
        """Block attributes describing what is special about this content ({} for plain text)."""
        result: dict[str, Any] = {}
        if self.media:
            methods = {item["method"] for item in self.media}
            result.update({
                "contains_media": True,
                "media": self.media,
                "media_tokens": sum(item["tokens"] for item in self.media),
                "media_only": not self.plain,
                "token_estimate": next(
                    (m for m in (UNAVAILABLE, ROUGH, PUBLISHED) if m in methods), PUBLISHED,
                ),
            })
        if self.document_text:
            result["document_text"] = True
        if self.scrubbed:
            result["binary_scrubbed"] = True
        return result


def _join(parts: list[Rendered]) -> Rendered:
    return Rendered(
        text="\n".join(p.text for p in parts if p.text),
        plain="\n".join(p.plain for p in parts if p.plain),
        media=[item for p in parts for item in p.media],
        document_text=any(p.document_text for p in parts),
        scrubbed=any(p.scrubbed for p in parts),
    )


def _text(value: str) -> Rendered:
    return Rendered(value, value)


def _dumped(value: Any) -> Rendered:
    scrubbed, changed = scrub_binary(value)
    text = json.dumps(scrubbed, ensure_ascii=False)
    return Rendered(text, text, scrubbed=changed)


def render_content(content: Any, model: str | None = None) -> Rendered:
    """Flatten a provider content value (string, part, or list of parts)."""
    if isinstance(content, str):
        return _text(content)
    if isinstance(content, list):
        return _join([render_content(part, model) for part in content])
    if isinstance(content, dict):
        content_type = content.get("type")
        source = content.get("source")
        if content_type == "document" and isinstance(source, dict):
            if source.get("type") == "text" and isinstance(source.get("data"), str):
                return Rendered(source["data"], source["data"], document_text=True)
            if source.get("type") == "content":
                rendered = render_content(source.get("content", ""), model)
                rendered.document_text = True
                return rendered
        if content_type in MEDIA_CONTENT_TYPES:
            marker, item = describe_media(content, model)
            return Rendered(marker, "", media=[item])
        if content_type in _TEXT_TYPES:
            return _text(str(content.get("text", "")))
        if content_type == "tool_result":
            return render_content(content.get("content", ""), model)
        if "text" in content:
            return _text(str(content["text"]))
        return _dumped(content)
    return _dumped(content) if content is not None else _text("")
