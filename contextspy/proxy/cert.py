from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path

_MITMPROXY_CA = Path.home() / ".mitmproxy" / "mitmproxy-ca.pem"


def cert_exists() -> bool:
    return _MITMPROXY_CA.exists()


def _extract_cert_pem() -> Path:
    """Return a NamedTemporaryFile path containing only the DER/PEM certificate
    extracted from the mitmproxy CA bundle (which also contains a private key).

    Uses the `cryptography` package (already a transitive dep of mitmproxy),
    so no external tools are required on any platform.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    pem_data = _MITMPROXY_CA.read_bytes()
    cert = x509.load_pem_x509_certificate(pem_data)
    cert_pem = cert.public_bytes(Encoding.PEM)

    tf = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
    tf.write(cert_pem)
    tf.close()
    return Path(tf.name)


def install_cert() -> tuple[bool, str]:
    """Attempt OS-specific system trust-store installation.

    Returns (success, message).
    """
    if not cert_exists():
        return False, "CA certificate not found at ~/.mitmproxy/mitmproxy-ca.pem. Run the proxy once to generate it."

    system = platform.system()

    # Extract certificate-only PEM (strips the private key that mitmproxy
    # bundles in mitmproxy-ca.pem, which confuses certutil / security).
    try:
        cert_path = _extract_cert_pem()
    except Exception as exc:
        return False, _manual_instructions(system) + f"\n\nFailed to parse certificate: {exc}"

    def _cleanup() -> None:
        try:
            cert_path.unlink()
        except Exception:
            pass

    try:
        if system == "Windows":
            result = subprocess.run(
                ["certutil", "-addstore", "-f", "Root", str(cert_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            _cleanup()
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
                    str(cert_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            _cleanup()
            if result.returncode == 0:
                return True, "Certificate installed in macOS system keychain."
            return False, _manual_instructions(system) + f"\n\nError: {result.stderr.strip()}"

        elif system == "Linux":
            import shutil

            dest = Path("/usr/local/share/ca-certificates/mitmproxy-ca.crt")
            shutil.copy(str(cert_path), str(dest))
            _cleanup()
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
            _cleanup()
            return False, _manual_instructions(system)

    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError) as exc:
        _cleanup()
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
