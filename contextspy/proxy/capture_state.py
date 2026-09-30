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
"""Process-wide capture pause flag shared by the API and the proxy addon.

Not persisted: capture always starts un-paused when contextspy restarts.
"""
from __future__ import annotations

import threading

_paused = threading.Event()


def is_paused() -> bool:
    return _paused.is_set()


def set_paused(paused: bool) -> None:
    if paused:
        _paused.set()
    else:
        _paused.clear()
