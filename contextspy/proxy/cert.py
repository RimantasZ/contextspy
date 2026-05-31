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

_MITMPROXY_DIR = Path.home() / ".mitmproxy"
_MITMPROXY_CA = _MITMPROXY_DIR / "mitmproxy-ca-cert.pem"


def cert_exists() -> bool:
    return _MITMPROXY_CA.exists()


def generate_cert() -> tuple[bool, str]:
    """Generate the mitmproxy CA certificate without starting the proxy.

    Returns (success, message).  If the cert already exists this is a no-op.
    """
    if cert_exists():
        return True, "CA certificate already exists."
    try:
        from mitmproxy.certs import CertStore
        CertStore.create_store(_MITMPROXY_DIR, "mitmproxy", key_size=2048)
        return True, f"CA certificate generated at {_MITMPROXY_CA}"
    except Exception as exc:
        return False, f"Failed to generate CA certificate: {exc}"


def _has_privileges(system: str) -> bool:
    """Return True if the current process has the privileges needed to install a system cert."""
    if system == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        import os
        return os.geteuid() == 0


def _insufficient_privileges_message(system: str) -> str:
    if system == "Windows":
        rerun_hint = (
            "To install automatically, re-run from an elevated Command Prompt:\n"
            "  contextspy install-cert"
        )
    else:
        rerun_hint = (
            "To install automatically, re-run as:\n"
            "  sudo contextspy install-cert"
        )
    return (
        "Insufficient privileges to install the CA certificate.\n"
        + rerun_hint
        + "\n\n"
        + _manual_instructions(system)
    )


def install_cert() -> tuple[bool, str]:
    """Generate (if needed) and install the mitmproxy CA into the system trust store.

    Returns (success, message).
    """
    if not cert_exists():
        ok, msg = generate_cert()
        if not ok:
            return False, msg

    system = platform.system()

    if not _has_privileges(system):
        return False, _insufficient_privileges_message(system)

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

    except PermissionError:
        return False, _insufficient_privileges_message(system)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
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
