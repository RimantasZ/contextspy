# contextspy.spec — PyInstaller one-file build spec
#
# Run from repo root after building the frontend:
#   npm --prefix ui run build
#   pyinstaller contextspy.spec
#
# Output: dist/contextspy  (single executable, ~60–120 MB uncompressed)

from PyInstaller.utils.hooks import collect_all

# mitmproxy and cryptography rely heavily on dynamic imports; collect everything.
mitmproxy_datas, mitmproxy_binaries, mitmproxy_hiddenimports = collect_all("mitmproxy")
crypto_datas, crypto_binaries, crypto_hiddenimports = collect_all("cryptography")

# Bundle the pre-built React UI assets.
# Path is relative to this spec file (repo root); PyInstaller resolves it at build time.
web_assets = [("contextspy/_web", "contextspy/_web")]

a = Analysis(
    ["contextspy/__main__.py"],
    pathex=[],
    binaries=mitmproxy_binaries + crypto_binaries,
    datas=web_assets + mitmproxy_datas + crypto_datas,
    hiddenimports=(
        mitmproxy_hiddenimports
        + crypto_hiddenimports
        + [
            # uvicorn uses lazy imports for its protocol/loop implementations
            "uvicorn.lifespan.on",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.loops.auto",
            # tiktoken encoding registry
            "tiktoken_ext.openai_public",
        ]
    ),
    hookspath=[],
    noarchive=False,
    excludes=["matplotlib", "pandas", "PIL", "tkinter"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="contextspy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # UPX causes false-positive AV detections; leave off
    console=True,
    onefile=True,
)
