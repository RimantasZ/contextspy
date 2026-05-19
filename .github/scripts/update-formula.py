#!/usr/bin/env python3
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

"""Patch version and sha256 values in Formula/contextspy.rb.

Usage:
    python3 update-formula.py <version> <arm64-sha256> <x86_64-sha256> <linux-sha256>
"""

import re
import sys

version, arm64, x86_64, linux = sys.argv[1:5]

with open("tap/Formula/contextspy.rb") as f:
    rb = f.read()

rb = re.sub(
    r'(version\s+")[^"]*(")',
    lambda m: f"{m.group(1)}{version}{m.group(2)}",
    rb,
)
rb = re.sub(
    r'(contextspy-macos-arm64\.tar\.gz"\n\s+sha256 ")[^"]*(")',
    lambda m: f"{m.group(1)}{arm64}{m.group(2)}",
    rb,
)
rb = re.sub(
    r'(contextspy-macos-x86_64\.tar\.gz"\n\s+sha256 ")[^"]*(")',
    lambda m: f"{m.group(1)}{x86_64}{m.group(2)}",
    rb,
)
rb = re.sub(
    r'(contextspy-linux-x86_64\.tar\.gz"\n\s+sha256 ")[^"]*(")',
    lambda m: f"{m.group(1)}{linux}{m.group(2)}",
    rb,
)

with open("tap/Formula/contextspy.rb", "w") as f:
    f.write(rb)

print(f"Formula updated to version {version}")
