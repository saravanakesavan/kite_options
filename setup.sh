#!/usr/bin/env bash
# =============================================================================
#  setup.sh — One-time setup for Kite Options Trading App (macOS)
#  Run once after cloning: bash setup.sh
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

info()  { echo -e "${GREEN}[setup]${NC} $*"; }
warn()  { echo -e "${YELLOW}[setup]${NC} $*"; }
error() { echo -e "${RED}[setup]${NC} $*"; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
ENV_FILE="$BACKEND_DIR/.env"

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon Macs
    if [[ -f "/opt/homebrew/bin/brew" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
info "Homebrew OK"

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────────
info "Checking Python..."
PYTHON_BIN=""
for py in python3.12 python3.11 python3; do
    if command -v "$py" &>/dev/null; then
        VER=$("$py" -c "import sys; print(sys.version_info[:2] >= (3,11))")
        if [[ "$VER" == "True" ]]; then
            PYTHON_BIN="$py"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    warn "Python 3.11+ not found. Installing via Homebrew..."
    brew install python@3.11
    PYTHON_BIN="$(brew --prefix)/bin/python3.11"
fi
info "Using Python: $($PYTHON_BIN --version)"

# ── 3. Node.js 18+ ───────────────────────────────────────────────────────────
info "Checking Node.js..."
if ! command -v node &>/dev/null || [[ $(node -e "process.exit(parseInt(process.version.slice(1)) < 18 ? 1 : 0)" 2>/dev/null; echo $?) -ne 0 ]]; then
    warn "Node.js 18+ not found. Installing via Homebrew..."
    brew install node@18
    export PATH="$(brew --prefix)/opt/node@18/bin:$PATH"
fi
info "Using Node: $(node --version)"

# ── 4. Python virtual environment ────────────────────────────────────────────
info "Creating Python virtual environment at $VENV_DIR ..."
"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

info "Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "$BACKEND_DIR/requirements.txt" -q
info "Python dependencies installed"

# ── 5. Frontend Node modules ─────────────────────────────────────────────────
info "Installing frontend dependencies..."
cd "$FRONTEND_DIR"
npm install --silent
info "Frontend dependencies installed"

# ── 6. .env file ─────────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    info "Creating $ENV_FILE from template..."
    cat > "$ENV_FILE" <<'ENVTEMPLATE'
# ── JWT ───────────────────────────────────────────────────────
# Change this to a long random string before going live:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=change-me-before-going-live

# ── Kite Connect API credentials ─────────────────────────────
# Get these from https://developers.kite.trade/
KITE_API_KEY=
KITE_API_SECRET=

# ── Database ──────────────────────────────────────────────────
# Default: SQLite file in the backend directory
DATABASE_URL=sqlite:///./trading_app.db

# ── CORS ─────────────────────────────────────────────────────
ALLOWED_ORIGINS=http://localhost:3000

# ── Trading limits (optional — defaults shown) ────────────────
MAX_INVESTMENT_PER_TRADE=10000
PROFIT_TARGET_MIN=300
PROFIT_TARGET_MAX=500
STOP_LOSS_PERCENTAGE=3
ENVTEMPLATE
    warn "Created $ENV_FILE — fill in KITE_API_KEY and KITE_API_SECRET before running the app."
else
    info ".env already exists — skipping"
fi

# ── 7. Verify test suite ──────────────────────────────────────────────────────
info "Running test suite to verify setup..."
cd "$BACKEND_DIR"
python -m pytest tests/ -q --tb=short 2>&1 | tail -5
info "Tests OK"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo "  Next steps:"
echo "  1. Edit backend/.env and fill in KITE_API_KEY and KITE_API_SECRET"
echo "  2. Run the app:   bash run.sh"
echo ""
