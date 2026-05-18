# PyPI & Homebrew Packaging Plan

## Core problem: bundling the frontend

The React build in `ui/dist/` is currently found via a relative path in `contextspy/api/main.py`:

```python
ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
```

That path resolves correctly in the repo but breaks after `pip install`. The fix is to embed the built assets **inside** the Python package so `setuptools` can ship them in the wheel.

---

## PyPI

### Step 1 — Relocate the built UI into the package

Change the build process so the React output lands at `contextspy/_web/` instead of `ui/dist/`:
- In `Makefile`, change the UI build target to copy `ui/dist/` → `contextspy/_web/`
- Or configure Vite's `outDir` in `vite.config.ts` to `../contextspy/_web`

### Step 2 — Declare the assets as package data in `pyproject.toml`

```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["contextspy*"]

[tool.setuptools.package-data]
contextspy = ["_web/**/*"]
```

Also add a `MANIFEST.in` so sdists include them:
```
recursive-include contextspy/_web *
```

### Step 3 — Fix the path in `main.py`

```python
ui_dist = Path(__file__).parent / "_web"
```

### Step 4 — Add release metadata to `pyproject.toml`

```toml
[project]
name = "contextspy"        # check availability on pypi.org first
version = "0.1.0"
description = "LLM context window analyser and proxy"
readme = "README.md"
license = { text = "MIT" }
authors = [{ name = "You", email = "you@example.com" }]
keywords = ["llm", "proxy", "tokens", "observability"]
classifiers = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: MIT License",
]
urls = { Homepage = "https://github.com/you/contextspy" }
```

### Step 5 — Build and publish

```bash
# Install build tools
pip install build twine

# Build the UI first, then the Python package
cd ui && npm run build
cp -r ui/dist contextspy/_web   # or via Makefile

# Build wheel + sdist
python -m build

# Upload (needs a PyPI account + API token)
twine upload dist/*
```

After that: `pip install contextspy` → `contextspy start`.

---

## Homebrew

### Option A — Homebrew tap (easiest, recommended)

Create a repo named `homebrew-contextspy` (the `homebrew-` prefix is required). Add a Formula:

```ruby
# homebrew-contextspy/Formula/contextspy.rb
class Contextspy < Formula
  include Language::Python::Virtualenv

  desc "LLM context window analyser and proxy"
  homepage "https://github.com/you/contextspy"
  url "https://files.pythonhosted.org/packages/.../contextspy-0.1.0.tar.gz"
  sha256 "..."
  license "MIT"

  depends_on "python@3.13"

  # Generate this list with: poet contextspy  (pip install homebrew-pypi-poet)
  resource "mitmproxy" do ... end
  resource "fastapi" do ... end
  # ... one resource block per dependency

  def install
    virtualenv_install_with_resources
  end

  test do
    system "#{bin}/contextspy", "--help"
  end
end
```

Users install it with:
```bash
brew tap you/contextspy
brew install contextspy
```

The `homebrew-pypi-poet` tool auto-generates the `resource` blocks from a local install.

### Option B — Standalone binary (cleanest UX, no Python required)

Bundle the Python runtime, all dependencies, and the `_web/` UI assets into a single
self-contained executable using **PyInstaller**. Distribute via a Homebrew tap pointing
at GitHub Release assets — same pattern used by `gh`, `ruff`, `uv`, and `ollama`.

Users install with:
```bash
brew tap rimantas/contextspy   # one-time
brew install contextspy
```
or on a machine without Homebrew:
```bash
curl -L https://github.com/rimantas/contextspy/releases/latest/download/contextspy-macos-arm64.tar.gz | tar xz
sudo mv contextspy /usr/local/bin/
```

---

#### B.1 — Build tool: PyInstaller

PyInstaller is the most proven option for mitmproxy-based tools. Nuitka (C compilation)
is faster at runtime but adds significant build complexity; defer unless startup time
becomes a concern.

Install in the dev venv:
```bash
uv pip install pyinstaller
```

**Known mitmproxy PyInstaller requirements:**
- `--collect-all mitmproxy` — mitmproxy uses dynamic imports for addons
- `--collect-all cryptography` — native extensions need explicit collection
- `--hidden-import mitmproxy.addons` and related modules
- `--hidden-import uvicorn.lifespan.on`

---

#### B.2 — PyInstaller spec file

Create `contextspy.spec` in the repo root. This is more reliable than CLI flags for
complex dependency graphs:

```python
# contextspy.spec
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect mitmproxy and cryptography (use dynamic imports extensively)
mitmproxy_datas, mitmproxy_binaries, mitmproxy_hiddenimports = collect_all("mitmproxy")
crypto_datas, crypto_binaries, crypto_hiddenimports = collect_all("cryptography")

# Bundle the built UI assets
web_assets = [
    (str(Path("contextspy/_web").resolve()), "contextspy/_web"),
]

a = Analysis(
    ["contextspy/__main__.py"],   # needs a __main__.py that calls cli.app()
    pathex=[],
    binaries=mitmproxy_binaries + crypto_binaries,
    datas=web_assets + mitmproxy_datas + crypto_datas,
    hiddenimports=(
        mitmproxy_hiddenimports
        + crypto_hiddenimports
        + [
            "uvicorn.lifespan.on",
            "uvicorn.protocols.http.auto",
            "uvicorn.protocols.websockets.auto",
            "uvicorn.loops.auto",
            "tiktoken_ext.openai_public",
        ]
    ),
    hookspath=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
    name="contextspy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX causes false-positive AV hits; leave off
    console=True,
    onefile=True,
)
```

**`contextspy/__main__.py`** (new file, ~3 lines):
```python
from contextspy.cli import app
if __name__ == "__main__":
    app()
```

**Local test build:**
```bash
npm --prefix ui run build          # ensure _web/ is fresh
pyinstaller contextspy.spec
./dist/contextspy --help
./dist/contextspy start --no-browser
```

---

#### B.3 — GitHub Actions: multi-platform build workflow

Create `.github/workflows/release-binary.yml`. Triggers on the same `v*` tag as the
PyPI publish workflow (or as a separate job in `publish.yml`).

Build matrix:
| Runner | Output filename |
|--------|-----------------|
| `macos-14` (Apple Silicon) | `contextspy-macos-arm64.tar.gz` |
| `macos-13` (Intel) | `contextspy-macos-x86_64.tar.gz` |
| `ubuntu-22.04` | `contextspy-linux-x86_64.tar.gz` |
| `windows-latest` (optional) | `contextspy-windows-x86_64.zip` |

```yaml
name: Release Binary

on:
  push:
    tags: ["v*"]

jobs:
  build:
    strategy:
      matrix:
        include:
          - os: macos-14
            arch: macos-arm64
          - os: macos-13
            arch: macos-x86_64
          - os: ubuntu-22.04
            arch: linux-x86_64

    runs-on: ${{ matrix.os }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - uses: actions/setup-node@v4
        with:
          node-version: "20"

      - name: Build frontend
        run: npm --prefix ui ci && npm --prefix ui run build

      - name: Install Python deps + PyInstaller
        run: |
          pip install uv
          uv venv
          uv pip install -e ".[dev]" pyinstaller

      - name: Build binary
        run: .venv/bin/pyinstaller contextspy.spec   # or python -m PyInstaller

      - name: Package
        run: |
          cd dist
          tar czf contextspy-${{ matrix.arch }}.tar.gz contextspy
          sha256sum contextspy-${{ matrix.arch }}.tar.gz > contextspy-${{ matrix.arch }}.tar.gz.sha256

      - name: Upload to GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/contextspy-${{ matrix.arch }}.tar.gz
            dist/contextspy-${{ matrix.arch }}.tar.gz.sha256
```

---

#### B.4 — Homebrew tap

Create a separate public repo named **`homebrew-contextspy`**
(the `homebrew-` prefix is mandatory for `brew tap` to work).

**`Formula/contextspy.rb`:**

```ruby
class Contextspy < Formula
  desc "LLM proxy that analyses token usage in context windows"
  homepage "https://github.com/rimantas/contextspy"
  version "0.1.0"
  license "Apache-2.0"

  on_macos do
    on_arm do
      url "https://github.com/rimantas/contextspy/releases/download/v#{version}/contextspy-macos-arm64.tar.gz"
      sha256 "REPLACE_ARM64_SHA256"
    end
    on_intel do
      url "https://github.com/rimantas/contextspy/releases/download/v#{version}/contextspy-macos-x86_64.tar.gz"
      sha256 "REPLACE_X86_64_SHA256"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/rimantas/contextspy/releases/download/v#{version}/contextspy-linux-x86_64.tar.gz"
      sha256 "REPLACE_LINUX_SHA256"
    end
  end

  def install
    bin.install "contextspy"
  end

  test do
    assert_match "LLM context window", shell_output("#{bin}/contextspy --help")
  end
end
```

**Update the formula on each release** — two options:
1. **Manual**: after CI uploads assets, copy sha256 values from the `.sha256` files into the formula and push.
2. **Automated**: add a workflow step that uses the GitHub API to push a formula update commit to `homebrew-contextspy` after the release assets are uploaded (requires a PAT with `repo` scope stored as a secret).

---

#### B.5 — Binary size considerations

A PyInstaller bundle for this stack will typically be **60–120 MB** before compression,
**30–60 MB** as a `.tar.gz`. Main contributors:
- mitmproxy + cryptography native libs: ~25 MB
- Python stdlib: ~15 MB
- tiktoken + numpy (if pulled in): ~10 MB
- UI assets (`_web/`): ~2 MB

To reduce size:
- `--exclude-module matplotlib,pandas,PIL` etc. if transitive deps pull them in
- Verify with `pyinstaller --log-level DEBUG` and inspect the `build/contextspy/warn-contextspy.txt`

---

#### B.6 — Pitfalls to expect with mitmproxy + PyInstaller

| Issue | Fix |
|-------|-----|
| `No module named 'mitmproxy.addons'` at runtime | Add `--collect-all mitmproxy` or explicit `hiddenimports` |
| SSL cert generation fails (mitmproxy CA) | Ensure `openssl` / `cryptography` native libs are bundled |
| `tiktoken` can't find encoding data | Set `TIKTOKEN_CACHE_DIR` to a writable path before first use, or pre-download and bundle the `.tiktoken` file as a data file in the spec |
| App crashes on macOS with `__NSCFConstantString` error | Add `--target-architecture arm64` on arm64 runners explicitly |
| Binary quarantined by macOS Gatekeeper | Need to code-sign with an Apple Developer certificate, or document `xattr -d com.apple.quarantine contextspy` as a workaround |

---

## Recommended order

1. Fix the `ui_dist` path and `package-data` → required for either target ✅ done
2. Publish to PyPI ✅ done
3. **Standalone binary (Option B)** — implement in this order:
   a. Add `contextspy/__main__.py`
   b. Write and locally test `contextspy.spec` (verify `contextspy start` works from the bundle)
   c. Add `.github/workflows/release-binary.yml`
   d. Create `homebrew-contextspy` repo with the formula template
   e. Tag a release → CI builds → copy sha256 values into formula → push
4. Option A (virtualenv formula) can be dropped now that Option B is planned — it offers
   no advantage over the PyPI install for Python users, and Option B is better for everyone else.
