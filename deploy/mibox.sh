#!/usr/bin/env bash
# Build on this machine, push a small bundle to the Mi Box, flip the `current` symlink and restart.
# Never build on the box (OOM). See mibox-ops.md.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${MIBOX_HOST:-mibox}"
BASE="/data/data/com.termux/files/home/apps/autotag"
STAGING="/data/data/com.termux/files/home/staging"
PORT="${AUTOTAG_PORT:-8780}"
BUNDLE="$(mktemp -d)/autotag-release.tar.gz"

npm --prefix frontend run build
tar -czf "$BUNDLE" --exclude='__pycache__' --exclude='tests' backend frontend/dist deploy/start.sh
scp "$BUNDLE" "$HOST:$STAGING/autotag-release.tar.gz"

ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
RELEASE_DIR="$BASE/releases/\$(date +%Y%m%d_%H%M%S)"
mkdir -p "\$RELEASE_DIR" "$BASE/shared/state"
tar -xzf "$STAGING/autotag-release.tar.gz" -C "\$RELEASE_DIR"
chmod +x "\$RELEASE_DIR/deploy/start.sh"
ln -sfn "\$RELEASE_DIR" "$BASE/current"
rm -f "$STAGING/autotag-release.tar.gz"
cd "$BASE/releases" && ls -1t | tail -n +3 | xargs -r rm -rf
if pm2 describe autotag >/dev/null 2>&1; then
  pm2 restart autotag
else
  AUTOTAG_PORT=$PORT pm2 start "$BASE/current/deploy/start.sh" --name autotag --interpreter bash
fi
pm2 save >/dev/null
sleep 4
curl -fsS "http://127.0.0.1:$PORT/api/health"
echo
pm2 logs autotag --lines 15 --nostream
REMOTE
