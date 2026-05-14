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

Use PyInstaller or Nuitka to compile everything — Python runtime, dependencies, and the bundled UI assets — into a single executable. Distribute via a Homebrew tap pointing at a GitHub Release asset:

```ruby
class Contextspy < Formula
  desc "LLM context window analyser and proxy"
  url "https://github.com/you/contextspy/releases/download/v0.1.0/contextspy-macos-arm64.tar.gz"
  sha256 "..."

  def install
    bin.install "contextspy"
  end
end
```

This is the approach used by tools like `gh`, `ruff`, and `uv`. It also avoids the `resource` boilerplate and works without Python on the user's machine. The downside is you need a CI pipeline (GitHub Actions) to build for `macos-arm64`, `macos-x86_64`, and `linux-x86_64`.

---

## Recommended order

1. Fix the `ui_dist` path and `package-data` → required for either target
2. Publish to PyPI first — it's straightforward and gives you the URL/sha256 needed for the Homebrew formula
3. Start with a **Homebrew tap** (Option A) for fast distribution — you can always migrate to a standalone binary later when you have CI set up
