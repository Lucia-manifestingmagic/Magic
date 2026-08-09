#!/usr/bin/env bash
#
# Publish the sanitised static demo to GitHub Pages.
#
#   ./scripts/publish_demo.sh
#
# What it does, in order:
#   1. Re-exports the demo (DEMO_MODE=1) so docs/ is current
#   2. Refuses to continue if any client identifier survived the sanitising
#   3. Force-pushes docs/ to a `gh-pages` branch — a standalone branch holding
#      only the built pages, so the source on `main` is never published
#   4. Enables GitHub Pages on that branch if it isn't already
#
# Requires `gh auth login` to have been run once.
#
# The gh-pages branch is a build artifact: it is force-pushed every time and
# its history is meaningless. `main` is never touched by this script.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PY="${PY:-.venv/bin/python}"
BRANCH="gh-pages"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

command -v gh >/dev/null 2>&1 || { echo "error: gh is not installed or not on PATH." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: run 'gh auth login' first." >&2; exit 1; }

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo "Repository: $REPO"

# 1 ── build ────────────────────────────────────────────────────────────────
echo "Exporting sanitised demo..."
"$PY" -m app.export >/dev/null

# 2 ── the safety gate ──────────────────────────────────────────────────────
# A Pages site is readable by anyone with the link, so this refuses to publish
# if the real client name or account prefix appears anywhere in the output.
REAL_NAME="$(DEMO_MODE=0 CLIENT_NAME= "$PY" -c 'from app import constants as C; print(C.CLIENT_NAME)')"
REAL_SHORT="$(DEMO_MODE=0 CLIENT_SHORT= "$PY" -c 'from app import constants as C; print(C.CLIENT_SHORT)')"

leaked=0
if grep -riqF "$REAL_NAME" docs/; then
  echo "error: the client name '$REAL_NAME' appears in docs/." >&2
  leaked=1
fi
# Case-sensitive: a short code like NKS would otherwise match inside "links".
if grep -rqF "$REAL_SHORT" docs/; then
  echo "error: the account prefix '$REAL_SHORT' appears in docs/." >&2
  leaked=1
fi
if [ "$leaked" -ne 0 ]; then
  echo "Refusing to publish. Fix the export, then run this again." >&2
  exit 1
fi
echo "Sanitising check passed — no client identifiers in docs/."

# 3 ── publish ──────────────────────────────────────────────────────────────
cp -R docs/. "$STAGING"/
cd "$STAGING"
git init -q -b "$BRANCH"
git add -A
git -c user.email="$(git -C "$REPO_DIR" config user.email || echo demo@example.com)" \
    -c user.name="$(git -C "$REPO_DIR" config user.name || echo Demo)" \
    commit -q -m "Publish sanitised ads dashboard demo

Static export rendered from generated fixture data for a fictional
distributor. No real advertiser, ad account or cost structure is
represented."

REMOTE="$(git -C "$REPO_DIR" remote get-url origin)"
git remote add origin "$REMOTE"
echo "Pushing $BRANCH..."
git push --force --quiet origin "$BRANCH"

# 4 ── enable Pages ─────────────────────────────────────────────────────────
cd "$REPO_DIR"
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  echo "Pages already enabled; pointing it at $BRANCH..."
  printf '{"source":{"branch":"%s","path":"/"}}' "$BRANCH" \
    | gh api -X PUT "repos/$REPO/pages" --input - >/dev/null
else
  echo "Enabling Pages on $BRANCH..."
  printf '{"source":{"branch":"%s","path":"/"}}' "$BRANCH" \
    | gh api -X POST "repos/$REPO/pages" --input - >/dev/null
fi

URL="$(gh api "repos/$REPO/pages" -q .html_url 2>/dev/null || echo '')"
echo
echo "Published. First build takes a minute or two."
echo "  ${URL:-https://$(echo "$REPO" | cut -d/ -f1 | tr 'A-Z' 'a-z').github.io/$(echo "$REPO" | cut -d/ -f2)/}"
