---
name: ls-rebuild-release
description: Rebuild all Lite Suite apps with pnpm dist, regenerate SHA-256 hashes from portable zips, update litesuite.dev manifest.json, and push GitHub releases. Use after icon changes, version bumps, or any change that requires rebuilding the installers.
user_invocable: true
---

# Rebuild & Release All Lite Suite Apps

Automates the full rebuild + release pipeline for the Lite AI Suite.

> **Note:** The directory paths below (`C:\Projects\*`) are reference paths for the LiteSuite monorepo layout. Substitute your own project root paths as needed. The GitHub org (`<your-github-org>/`) and manifest path (`litesuite.dev`) should also be updated to match your setup.

## Apps

| App          | Directory                  | Build Output | Zip Pattern              |
| ------------ | -------------------------- | ------------ | ------------------------ |
| LiteEditor   | `C:\Projects\LiteEditor`   | `dist/`      | `LiteEditor-*-win.zip`   |
| LiteSpeak    | `C:\Projects\LiteSpeak`    | `dist/`      | `LiteSpeak-*-win.zip`    |
| LiteTerminal | `C:\Projects\LiteTerminal` | `dist/`      | `LiteTerminal-*-win.zip` |
| LiteImage    | `C:\Projects\LiteImage`    | `release/`   | `LiteImage-*-win.zip`    |

Non-Electron apps (LiteBench, LiteYT) are not built with electron-builder — skip them.

## Steps

### 1. Rebuild all Electron desktop apps in parallel

Spawn parallel agents (one per app) to run `pnpm dist` in each app directory.

Command for each: `cd <dir> && pnpm dist`

Each produces:

- NSIS installer: `<ProductName> Setup <version>.exe` (for direct install)
- Portable zip: `<ProductName>-<version>-win.zip` (for LiteCore installer)
- Unpacked dir: `win-unpacked/` (for dev use)

### 2. Generate SHA-256 hashes and file sizes from the PORTABLE ZIPS

**IMPORTANT:** Hash the `.zip` files, NOT the NSIS `.exe` installers. The LiteCore installer downloads and extracts these zips.

```bash
for f in \
  "C:/Projects/LiteEditor/dist/LiteEditor-"*"-win.zip" \
  "C:/Projects/LiteSpeak/dist/LiteSpeak-"*"-win.zip" \
  "C:/Projects/LiteTerminal/dist/LiteTerminal-"*"-win.zip" \
  "C:/Projects/LiteImage/release/LiteImage-"*"-win.zip"; do
  if [ -f "$f" ]; then
    size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null)
    hash=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)
    echo "$(basename "$f")|$size|$hash"
  fi
done
```

### 3. Update manifest.json

Edit `C:\Projects\litesuite.dev\public\manifest.json`:

- Update `updatedAt` to today's date
- For each app with a new zip:
  - Update `sha256` with the hash from step 2
  - Update `size` with the byte count from step 2
  - Update `version` if the app version changed
  - Update `downloadUrl` if version changed (format: `https://litesuite.dev/api/download/app?id=<appId>&v=<version>`)

Leave `comingSoon` apps untouched.

### 4. Push GitHub releases

For each app, delete old release and create new with the **portable zip** as the asset:

```bash
cd <app-dir>
VERSION=$(node -p "require('./package.json').version")
TAG="v$VERSION"
gh release delete "$TAG" --yes 2>/dev/null
gh release create "$TAG" "<path-to-zip>" --title "<AppName> $VERSION" --notes "Portable build for LiteCore installer" --latest
```

Repos (all under `<your-github-org>/`):

- LiteEditor, LiteSpeak, LiteTerminal, LiteImage

The download endpoint at `litesuite.dev/api/download/app/route.ts` takes `release.assets[0]` — the zip must be the **only or first** asset in the release.

### 5. Deploy manifest to litesuite.dev

litesuite.dev deploys via git push (CI/CD):

```bash
cd C:/Projects/litesuite.dev
git add public/manifest.json
git commit -m "Update manifest: new release hashes"
git push
```

**Do NOT manually build/deploy** — the CI/CD pipeline handles it.

## Notes

- Use `pnpm` (not npm/yarn) in all Lite Suite projects
- Use `python` not `python3` on Windows
- LiteImage outputs to `release/` not `dist/` (set via electron-builder config)
- Service apps (LiteMemory, LiteBench) don't use electron-builder — they're plain Node.js
- The LiteCore installer (`orchestrator.ts`) downloads the zip, extracts it flat, and looks for `ProductName.exe` via `findAppExe()`
- If a release zip contains files at root level (no nesting), the installer works correctly
