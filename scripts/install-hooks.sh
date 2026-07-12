#!/usr/bin/env bash
# Install git hooks from scripts/. Run once per checkout.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
HOOK="$ROOT/.git/hooks/pre-commit"
cat > "$HOOK" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install-hooks.sh — real work lives in
# scripts/precommit.sh so it's checked in.
exec "$(git rev-parse --show-toplevel)/scripts/precommit.sh"
HOOK
chmod +x "$HOOK"
echo "✓ Installed $HOOK"
