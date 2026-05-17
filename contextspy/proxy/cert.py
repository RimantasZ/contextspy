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

import platform
import subprocess
import sys
from pathlib import Path

_MITMPROXY_CA = Path.home() / ".mitmproxy" / "mitmproxy-ca.pem"


def cert_exists() -> bool:
    return _MITMPROXY_CA.exists()


def install_cert() -> tuple[bool, str]:
    """Attempt OS-specific system trust-store installation.

    Returns (success, message).
    """
    if not cert_exists():
        return False, "CA certificate not found at ~/.mitmproxy/mitmproxy-ca.pem. Run the proxy once to generate it."

    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["certutil", "-addstore", "-f", "Root", str(_MITMPROXY_CA)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, "Certificate installed in Windows Root store."
            return False, _manual_instructions(system) + f"\n\nError: {result.stderr.strip()}"

        elif system == "Darwin":
            result = subprocess.run(
                [
                    "security",
                    "add-trusted-cert",
                    "-d",
                    "-r",
                    "trustRoot",
                    "-k",
                    "/Library/Keychains/System.keychain",
                    str(_MITMPROXY_CA),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, "Certificate installed in macOS system keychain."
            return False, _manual_instructions(system) + f"\n\nError: {result.stderr.strip()}"

        elif system == "Linux":
            import shutil

            dest = Path("/usr/local/share/ca-certificates/mitmproxy-ca.crt")
            shutil.copy(str(_MITMPROXY_CA), str(dest))
            result = subprocess.run(
                ["update-ca-certificates"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True, "Certificate installed via update-ca-certificates."
            return False, _manual_instructions(system) + f"\n\nError: {result.stderr.strip()}"

        else:
            return False, _manual_instructions(system)

    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
        return False, _manual_instructions(system) + f"\n\nException: {exc}"


def _manual_instructions(system: str) -> str:
    cert_path = str(_MITMPROXY_CA)
    if system == "Windows":
        return (
            "Manual installation:\n"
            f"  certutil -addstore -f Root \"{cert_path}\"\n"
            "Or double-click the .pem file → Install Certificate → Local Machine → Trusted Root CAs."
        )
    elif system == "Darwin":
        return (
            "Manual installation:\n"
            f'  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "{cert_path}"'
        )
    else:
        return (
            "Manual installation:\n"
            f"  sudo cp {cert_path} /usr/local/share/ca-certificates/mitmproxy-ca.crt\n"
            "  sudo update-ca-certificates"
        )
