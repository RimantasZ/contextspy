# Release Guide

## Prerequisites (one-time setup)

### 1. Update author metadata in `pyproject.toml`

Replace the placeholder values:

```toml
authors = [
    { name = "Your Name", email = "you@example.com" },
]

[project.urls]
Homepage = "https://github.com/you/contextspy"
"Bug Tracker" = "https://github.com/you/contextspy/issues"
```

### 2. Create a PyPI account

Go to <https://pypi.org/account/register/> and create an account if you don't have one.

### 3. Create the PyPI project and configure Trusted Publishing

Trusted Publishing lets GitHub Actions publish to PyPI without storing an API token.

1. Log in to <https://pypi.org>
2. Go to **Account settings → Publishing → Add a new pending publisher**
3. Fill in:
   - **PyPI project name:** `contextspy`
   - **Owner:** your GitHub username
   - **Repository:** `contextspy`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
4. Save.

### 4. Create the `pypi` environment in your GitHub repo

1. Go to your GitHub repo → **Settings → Environments → New environment**
2. Name it `pypi`
3. Optionally add a protection rule (e.g. require a reviewer before deploying)

---

## Making a release

### Step 1 — Update the version

Edit `pyproject.toml`:

```toml
version = "0.2.0"   # bump to the new version
```

Follow [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.

### Step 2 — Commit the version bump

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push
```

### Step 3 — Tag the release

```bash
git tag v0.2.0
git push --tags
```

Pushing the tag triggers the `.github/workflows/publish.yml` workflow automatically.

### Step 4 — Verify the workflow

Go to your GitHub repo → **Actions** → the running `Publish to PyPI` workflow.

The workflow does the following:
1. Builds the React frontend (`npm ci && npm run build`) — output goes to `contextspy/_web/`
2. Builds the Python wheel and sdist (`python -m build`)
3. Publishes both to PyPI via OIDC trusted publishing

If the workflow succeeds, the package will be available at:
`https://pypi.org/project/contextspy/`

---

## Manual build (local testing before release)

To build and inspect the package locally without publishing:

```bash
# 1. Build the frontend
cd ui
npm ci
npm run build
cd ..

# 2. Install build tools
pip install build

# 3. Build wheel + sdist
python -m build

# dist/ will contain:
#   contextspy-X.Y.Z-py3-none-any.whl
#   contextspy-X.Y.Z.tar.gz
```

### Inspect the wheel contents

```bash
pip install wheel
wheel unpack dist/contextspy-*.whl --dest /tmp/wheel-check
ls /tmp/wheel-check/contextspy/_web/   # should contain index.html and assets/
```

### Test install from the local wheel

```bash
pip install dist/contextspy-*.whl
contextspy --help
contextspy start
```

### Publish manually (without GitHub Actions)

If you prefer to publish manually instead of using the workflow:

```bash
pip install twine

# Upload to PyPI (will prompt for username/password or API token)
twine upload dist/*

# Or use an API token (generate at pypi.org → Account settings → API tokens)
twine upload dist/* --username __token__ --password pypi-...
```

---

## Patch releases

For a hotfix or patch:

```bash
# bump patch version in pyproject.toml  e.g. 0.2.0 → 0.2.1
git add pyproject.toml
git commit -m "chore: bump version to 0.2.1"
git push
git tag v0.2.1
git push --tags
```

---

## Release checklist

- [ ] `pyproject.toml` version bumped
- [ ] `CHANGELOG` or release notes updated (if maintained)
- [ ] All tests passing locally
- [ ] `git push` before tagging (tag should be on the latest commit)
- [ ] Tag pushed (`git push --tags`)
- [ ] GitHub Actions workflow passed
- [ ] New version visible at pypi.org/project/contextspy
