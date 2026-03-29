#!/usr/bin/env bash
# =============================================================================
#  run.sh — Start the Kite Options Trading App (macOS)
#  Run every time:  bash run.sh
#  Stop:            Ctrl+C  (kills both backend and frontend)
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[run]${NC} $*"; }
warn()  { echo -e "${YELLOW}[run]${NC} $*"; }
error() { echo -e "${RED}[run]${NC} $*"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
ENV_FILE="$BACKEND_DIR/.env"

# ── Pre-flight checks ─────────────────────────────────────────────────────────

if [[ ! -d "$VENV_DIR" ]]; then
    error "Virtual environment not found. Run setup first:  bash setup.sh"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    error ".env file not found. Run setup first:  bash setup.sh"
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    error "node_modules not found. Run setup first:  bash setup.sh"
fi

# Warn if Kite credentials are missing (app will still start in limited mode)
if grep -qE "^KITE_API_KEY=$" "$ENV_FILE" || ! grep -q "KITE_API_KEY" "$ENV_FILE"; then
    warn "KITE_API_KEY not set in backend/.env — Kite features will not work"
fi

# ── Port helpers ──────────────────────────────────────────────────────────────

kill_port() {
    local port="$1"
    local pid
    pid=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [[ -n "$pid" ]]; then
        warn "Port $port in use (PID $pid) — killing..."
        kill -9 $pid 2>/dev/null || true
        sleep 0.5
    fi
}

# ── Activate venv ─────────────────────────────────────────────────────────────

source "$VENV_DIR/bin/activate"

# ── DB schema check — reset if stale ─────────────────────────────────────────
# SQLite DBs created before column additions fail at runtime.
# Pass --reset-db to wipe and recreate, or we auto-detect obvious mismatches.

DB_FILE="$BACKEND_DIR/trading_app.db"

if [[ "${1:-}" == "--reset-db" ]]; then
    warn "--reset-db flag passed: removing $DB_FILE"
    rm -f "$DB_FILE"
    info "Database will be recreated on startup"
elif [[ -f "$DB_FILE" ]]; then
    # Quick schema check using sqlite3 (built into macOS — no Python env needed)
    COLS=$(sqlite3 "$DB_FILE" "PRAGMA table_info(users);" 2>/dev/null | awk -F'|' '{print $2}')
    SCHEMA_OK="OK"
    for col in id username email hashed_password is_active access_token; do
        if ! echo "$COLS" | grep -qx "$col"; then
            SCHEMA_OK="STALE:missing $col"
            break
        fi
    done
    if [[ "$SCHEMA_OK" == OK ]]; then
        info "Database schema OK"
    else
        warn "Database schema mismatch ($SCHEMA_OK)"
        warn "Backing up old DB to trading_app.db.bak and recreating..."
        cp "$DB_FILE" "${DB_FILE}.bak"
        rm -f "$DB_FILE"
        info "Old database backed up — you will need to re-register your account"
    fi
fi

# ── Free ports if occupied ────────────────────────────────────────────────────

kill_port 8000
kill_port 3000

# ── Track child PIDs for clean shutdown ───────────────────────────────────────

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo ""
    info "Shutting down..."
    [[ -n "$BACKEND_PID" ]]  && kill "$BACKEND_PID"  2>/dev/null || true
    [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    info "Stopped."
}
trap cleanup INT TERM EXIT

# ── Start backend ─────────────────────────────────────────────────────────────

info "Starting backend on http://localhost:8000 ..."
cd "$BACKEND_DIR"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Wait until backend is up (up to 15 s)
info "Waiting for backend to be ready..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/docs > /dev/null 2>&1; then
        info "Backend ready"
        break
    fi
    sleep 0.5
    if [[ $i -eq 30 ]]; then
        error "Backend did not start within 15 s. Check logs above."
    fi
done

# ── Start frontend ────────────────────────────────────────────────────────────

info "Starting frontend on http://localhost:3000 ..."
cd "$FRONTEND_DIR"
npm start &
FRONTEND_PID=$!

# ── Open browser ─────────────────────────────────────────────────────────────

sleep 3
if command -v open &>/dev/null; then
    open http://localhost:3000
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  Kite Options Trading App running${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Frontend  →  ${GREEN}http://localhost:3000${NC}"
echo -e "  Backend   →  ${GREEN}http://localhost:8000${NC}"
echo -e "  API docs  →  ${GREEN}http://localhost:8000/docs${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Press Ctrl+C to stop both servers"
echo ""

# ── Keep running until Ctrl+C ─────────────────────────────────────────────────

wait $BACKEND_PID $FRONTEND_PID
