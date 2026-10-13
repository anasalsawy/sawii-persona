#!/usr/bin/env bash
# Build the companion client and drop it where the backend serves it from.
#   ./ui/build.sh            build
#   ./ui/build.sh --dev      vite dev server instead (proxying /v1 to :8800)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/webview"
[ -d node_modules ] || npm install --no-audit --no-fund
npm run build
rm -rf "$here/../src/sawii_persona/web/companion"
cp -r "$here/dist" "$here/../src/sawii_persona/web/companion"
echo "companion client built -> src/sawii_persona/web/companion"
