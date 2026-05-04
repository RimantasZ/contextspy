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
            # macOS `security add-trusted-cert` can fail if the PEM contains
            # both a private key and a certificate. Extract the certificate
            # portion to a temporary file first (using OpenSSL), then install.
            import tempfile

            try:
                with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tf:
                    temp_cert_path = Path(tf.name)

                ext = subprocess.run(
                    ["openssl", "x509", "-in", str(_MITMPROXY_CA), "-outform", "pem", "-out", str(temp_cert_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if ext.returncode != 0:
                    # OpenSSL failed to parse the file; fall back to manual instructions.
                    try:
                        temp_cert_path.unlink()
                    except Exception:
                        pass
                    return False, _manual_instructions(system) + f"\n\nOpenSSL error: {ext.stderr.strip()}"

                result = subprocess.run(
                    [
                        "security",
                        "add-trusted-cert",
                        "-d",
                        "-r",
                        "trustRoot",
                        "-k",
                        "/Library/Keychains/System.keychain",
                        str(temp_cert_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                try:
                    temp_cert_path.unlink()
                except Exception:
                    pass

                if result.returncode == 0:
                    return True, "Certificate installed in macOS system keychain."
                return False, _manual_instructions(system) + f"\n\nError: {result.stderr.strip()}"
            except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
                return False, _manual_instructions(system) + f"\n\nException: {exc}"

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
