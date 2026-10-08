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
"""Small shared derivations over provider-reported usage."""
from __future__ import annotations


def cached_share_pct(cache_read_tokens: int | None, provider_input_tokens: int | None) -> float | None:
    """Share of provider-reported input tokens served from the prompt cache, as a percentage.

    ``None`` when the provider did not report input tokens (or reported zero). Single definition
    used by the request detail ("Cached share") and the session trend chart.
    """
    if provider_input_tokens is None or provider_input_tokens <= 0:
        return None
    return (cache_read_tokens or 0) / provider_input_tokens * 100
