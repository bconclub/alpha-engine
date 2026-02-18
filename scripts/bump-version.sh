#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Auto version bump — called by git pre-commit hook
#
# Single Alpha version stored in engine/VERSION. Bumps on every
# commit regardless of which directory changed. Patch rolls over
# at 10:  3.10.9 → 3.11.0
#
# The VERSION file is auto-staged so it's included in the commit.
# ═══════════════════════════════════════════════════════════════════

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
VERSION_FILE="$REPO_ROOT/engine/VERSION"

bump_version() {
    local current
    current=$(cat "$VERSION_FILE" 2>/dev/null || echo "3.0.0")

    IFS='.' read -r major minor patch <<< "$current"
    patch=$((patch + 1))

    if [ "$patch" -ge 10 ]; then
        patch=0
        minor=$((minor + 1))
    fi

    local new_version="$major.$minor.$patch"
    echo "$new_version" > "$VERSION_FILE"
    git add "$VERSION_FILE"
    echo "  Alpha: $current → $new_version"
}

# Skip if this is a version-only commit (prevent double-bump)
ONLY_VERSIONS=$(git diff --cached --name-only | grep -v 'VERSION' | head -1 || true)
if [ -z "$ONLY_VERSIONS" ]; then
    exit 0
fi

echo "📦 Bumping Alpha version..."
bump_version
echo "✅ Version bump complete"
