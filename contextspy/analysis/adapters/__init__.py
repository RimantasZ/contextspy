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
"""Provider adapter registry.

Each adapter turns one wire format (Anthropic Messages, OpenAI Chat
Completions, OpenAI Responses, Ollama native) into provider-agnostic
``Block``/``Usage`` objects. Adding a new provider or wire format means
adding one new adapter module here — nothing else in the pipeline needs to
change (``get_adapter`` dispatches by endpoint path, same as the old
``_wire_format`` it replaces).
"""
from __future__ import annotations

from contextspy.analysis.adapters.base import REGISTRY, WireFormatAdapter, get_adapter, register
from contextspy.analysis.adapters.anthropic import AnthropicAdapter
from contextspy.analysis.adapters.ollama import OllamaAdapter
from contextspy.analysis.adapters.openai_chat import OpenAIChatAdapter
from contextspy.analysis.adapters.openai_responses import OpenAIResponsesAdapter

# Registration order = dispatch priority (see get_adapter / test_wire_format
# regressions for why /messages must be checked before /responses, etc).
register(AnthropicAdapter())
register(OpenAIChatAdapter())
register(OpenAIResponsesAdapter())
register(OllamaAdapter())

__all__ = ["REGISTRY", "WireFormatAdapter", "get_adapter"]
