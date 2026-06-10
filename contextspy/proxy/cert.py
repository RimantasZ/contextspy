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
_MITMPROXY_KEY = _MITMPROXY_DIR / "mitmproxy-ca.pem"


def cert_exists() -> bool:
    """Return True only when both the CA cert and its private key are present."""
    return _MITMPROXY_CA.exists() and _MITMPROXY_KEY.exists()


def generate_cert() -> tuple[bool, str]:
    """Generate the mitmproxy CA certificate without starting the proxy.

    Returns (success, message).  If the cert already exists and is valid, no-op.
    If the files exist but are corrupted, they are removed and regenerated.
    """
    if cert_exists():
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            load_pem_private_key(_MITMPROXY_KEY.read_bytes(), password=None)
            return True, "CA certificate already exists."
        except PermissionError:
            return False, (
                f"CA key is not readable ({_MITMPROXY_KEY}).\n"
                f"Files are likely owned by root from a previous sudo run.\n"
                f"Fix with:  sudo chown -R $USER ~/.mitmproxy/"
            )
        except Exception:
            pass  # corrupted key — fall through to regenerate

    # Remove any partial or corrupted files before regenerating
    for f in (_MITMPROXY_CA, _MITMPROXY_KEY):
        if f.exists():
            try:
                f.unlink()
            except PermissionError:
                return False, (
                    f"Cannot remove CA files ({_MITMPROXY_DIR}) — owned by root.\n"
                    f"Fix with:  sudo rm -rf ~/.mitmproxy/"
                )
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


def _install_cert_windows() -> tuple[bool, str]:
    # Try CurrentUser\Root first — no elevation needed; works for both
    # Chromium (VS Code main process) and Node.js with --use-system-ca.
    result = subprocess.run(
        ["certutil", "-user", "-addstore", "-f", "Root", str(_MITMPROXY_CA)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode == 0:
        return True, "Certificate installed in Windows current-user Root store."
    # Fall back to machine-wide store if we have admin privileges.
    if _has_privileges("Windows"):
        result = subprocess.run(
            ["certutil", "-addstore", "-f", "Root", str(_MITMPROXY_CA)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, "Certificate installed in Windows system Root store."
    return False, _manual_instructions("Windows") + f"\n\nError: {result.stderr.strip()}"


def install_cert() -> tuple[bool, str]:
    """Generate (if needed) and install the mitmproxy CA into the system trust store.

    Returns (success, message).
    """
    if not cert_exists():
        ok, msg = generate_cert()
        if not ok:
            return False, msg

    system = platform.system()

    # Windows uses a two-step approach (user store first, no elevation needed).
    if system == "Windows":
        try:
            return _install_cert_windows()
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
            return False, _manual_instructions(system) + f"\n\nException: {exc}"

    if not _has_privileges(system):
        return False, _insufficient_privileges_message(system)

    try:
        if system == "Darwin":
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
