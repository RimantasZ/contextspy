# Mixed PyInstaller packaging: onedir for Homebrew/dpkg, onefile for raw downloads + Windows

## Context

`contextspy start` (and even `contextspy --version`) has a consistent, non-data-dependent
multi-second startup delay for Homebrew-installed users. Root cause, diagnosed earlier this
session: `contextspy.spec` builds a PyInstaller **onefile** executable (`onefile=True`). Onefile
executables self-extract their entire bundle (mitmproxy + cryptography + tiktoken + the bundled
React UI — "~60-120 MB uncompressed" per the spec's own comment) to a temp directory on **every
single launch**, before any application code runs — this matches every symptom observed (every
start, independent of DB size, independent of sudo, even affects `--version`).

Fix: switch to PyInstaller **onedir** mode (extracted once, near-instant startup on every
subsequent run) — but only for the channels where users launch the binary repeatedly over time
(Homebrew, `.deb`/dpkg). Raw binary downloads (the "Download binary archive" table in
`docs/install.md`) and the Windows build stay **onefile**, since a single portable, movable file
is the actual value proposition of that channel, and onedir requires keeping the executable
alongside a sibling `_internal/`-style folder (not movable on its own).

Confirmed via reading `.github/workflows/release-binary.yml`: today **all four channels
(raw macOS/Linux tarball, raw Windows zip, Homebrew, `.deb`) share one single onefile PyInstaller
build per platform** — there is no separate build step per channel yet. PyInstaller can define
two output targets (onefile `EXE` + onedir `EXE(exclude_binaries=True)` + `COLLECT`) sharing one
`Analysis()` in a single spec file, so the expensive dependency-collection step runs once; this
only costs a bit of extra packaging time, and only on release-tag pushes (not routine CI).

## 1. `contextspy.spec` — add a second onedir output

Keep the existing onefile `EXE(..., name="contextspy", onefile=True)` (feeds raw downloads +
Windows, unchanged). Add, sharing the same `a = Analysis(...)`:

```python
exe_onedir = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="contextspy",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True,
)
coll = COLLECT(
    exe_onedir, a.binaries, a.datas,
    name="contextspy-onedir",   # -> dist/contextspy-onedir/  (contains contextspy + _internal/)
)
```

One `pyinstaller contextspy.spec` invocation now produces both `dist/contextspy` (onefile) and
`dist/contextspy-onedir/` (onedir folder).

## 2. `release-binary.yml` — macOS + Linux matrix jobs only (Windows job untouched)

- Existing "Package and checksum (Unix)" step: unchanged — still tars `dist/contextspy` →
  `contextspy-${arch}.tar.gz` (raw download artifact, as today).
- **New step**, macOS + Linux only: tar `dist/contextspy-onedir/` → `contextspy-onedir-${arch}.tar.gz`
  + `.sha256`, uploaded as an additional release asset (consumed only by Homebrew/`.deb`, not
  listed in the user-facing download table).
- **`.deb` build step** (`fpm`): change the source mapping from
  `dist/contextspy=/usr/bin/contextspy` (single file) to installing the onedir folder plus a
  working entrypoint symlink, e.g.:
  ```bash
  mkdir -p pkgroot/usr/lib/contextspy pkgroot/usr/bin
  cp -r dist/contextspy-onedir/* pkgroot/usr/lib/contextspy/
  ln -s /usr/lib/contextspy/contextspy pkgroot/usr/bin/contextspy
  fpm -s dir -t deb ... pkgroot/=/
  ```
  (same idea used by other apps that ship a directory bundle via `.deb`, e.g. VS Code).

## 3. `update-formula.py` + `brew-formula/contextspy.rb`

- `update-formula.py`'s regex substitutions currently match the literal filenames
  `contextspy-macos-arm64.tar.gz` / `contextspy-linux-x86_64.tar.gz` — update both patterns (and
  the `release-binary.yml` `update-formula` job's `gh release download --pattern` /
  `update-formula.py` invocation args) to reference the new `contextspy-onedir-*.tar.gz` sha256
  files instead.
- `brew-formula/contextspy.rb`:
  - Both `url` lines (macOS arm64 **and** the Linux-Homebrew entry — "brew" covers Homebrew on
    Linux too, and it can reuse the same onedir tarball already built for `.deb`, no extra
    artifact needed) → point at `contextspy-onedir-*.tar.gz`.
  - `install` block: `bin.install "contextspy"` → `libexec.install Dir["*"]` +
    `bin.install_symlink libexec/"contextspy"` (standard Homebrew pattern for a directory-shaped
    app).
  - `caveats` block: the Gatekeeper `xattr -d com.apple.quarantine #{bin}/contextspy` becomes
    recursive against the libexec directory: `xattr -dr com.apple.quarantine #{libexec}` — onedir
    mode has multiple bundled `.so`/`.dylib` files that each carry the quarantine flag, and
    Gatekeeper checks them individually when the main binary loads them.

## 4. `docs/install.md` — wording-only fix

- Linux `.deb` section: "Installs to `/usr/bin/contextspy`" (currently literally true) becomes
  "Installs to `/usr/lib/contextspy/` with a `contextspy` command on your `PATH`" or similar — the
  documented user commands (`sudo dpkg -i ...`, `contextspy help`) don't change.
- No other section changes — Homebrew's documented commands, the raw-download table, the
  Windows instructions, and all troubleshooting entries about `xattr`/Gatekeeper for the
  **raw-download** path stay as-is (those still describe the onefile artifact).

## Verification

1. Build locally: `pyinstaller contextspy.spec`, confirm both `dist/contextspy` (onefile) and
   `dist/contextspy-onedir/contextspy` (+ bundled libs alongside it) are produced from one run.
2. Time `./dist/contextspy-onedir/contextspy --version` vs `./dist/contextspy --version` to
   confirm onedir actually resolves the startup delay (should be near-instant vs. the multi-second
   onefile extraction).
3. Dry-run the `.deb` build locally with `fpm` using the onedir folder + symlink layout; install
   into a throwaway container, confirm `contextspy start`/`--version` work via `/usr/bin/contextspy`.
4. Test the Homebrew formula changes against a local tap clone (`brew install --build-from-source`
   pointing at a local tarball) before touching the real `homebrew-contextspy` tap.
5. Do a full dry run via `workflow_dispatch` on a test tag (not a real version tag) before the
   next real release, to confirm the release workflow's new steps (onedir tar, `.deb` symlink,
   formula sha256 patching) all succeed end-to-end.
6. Confirm `docs/install.md` still accurately describes both the Homebrew/`.deb` (onedir, now
   fixed-wording) and raw-download/Windows (onefile, unchanged) paths.
