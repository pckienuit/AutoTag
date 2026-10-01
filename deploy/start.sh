#!/data/data/com.termux/files/usr/bin/bash
# Started by pm2 on the Mi Box. Code comes from the `current` release symlink;
# .env and the SQLite/image state live in shared/ so they survive releases.
BASE="$HOME/apps/autotag"
export AUTOTAG_ENV_FILE="$BASE/shared/.env"
export AUTOTAG_STATE_DIR="$BASE/shared/state"
export AUTOTAG_STATIC_DIR="$BASE/current/frontend/dist"
cd "$BASE/current" || exit 1
exec python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${AUTOTAG_PORT:-8780}" --no-access-log
