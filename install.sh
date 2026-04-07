#!/usr/bin/env bash
# Flint installer — single command to install and run Flint
# Usage: curl -fsSL <url>/install.sh | bash
set -euo pipefail

FLINT_HOME="${FLINT_HOME:-$HOME/.flint}"
REPO_URL="${FLINT_REPO_URL:-https://github.com/sohan-shingade/flint.git}"
MIN_PYTHON="3.10"
MIN_NODE="18"

# ── Helpers ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
DIM='\033[2m'
RESET='\033[0m'

step=0
total_steps=7

progress() {
    step=$((step + 1))
    printf "${AMBER}[%d/%d]${RESET} %s\n" "$step" "$total_steps" "$1"
}

success() {
    printf "  ${GREEN}✓${RESET} %s\n" "$1"
}

fail() {
    printf "  ${RED}✗${RESET} %s\n" "$1"
    exit 1
}

info() {
    printf "  ${DIM}%s${RESET}\n" "$1"
}

version_gte() {
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# ── Step 1: Detect OS ────────────────────────────────────────

progress "Detecting OS..."

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin) OS_TYPE="macos" ;;
    Linux)  OS_TYPE="linux" ;;
    *)      fail "Unsupported OS: $OS. Flint supports macOS and Linux." ;;
esac

if [ "$OS_TYPE" = "linux" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
    elif command -v pacman >/dev/null 2>&1; then
        PKG_MGR="pacman"
    else
        fail "No supported package manager found (apt, dnf, pacman)"
    fi
fi

success "$OS_TYPE $ARCH"

# ── Step 2: Check/install Python ─────────────────────────────

progress "Checking Python ${MIN_PYTHON}+..."

install_python() {
    info "Installing Python..."
    if [ "$OS_TYPE" = "macos" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            printf "\n  Homebrew is required to install Python on macOS but is not installed.\n"
            printf "  Install Homebrew? (https://brew.sh) [y/N] "
            read -r REPLY
            if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
            else
                fail "Homebrew is required to install Python on macOS. Install it manually from https://brew.sh or install Python ${MIN_PYTHON}+ yourself."
            fi
        fi
        brew install python@3.12
    elif [ "$PKG_MGR" = "apt" ]; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip
    elif [ "$PKG_MGR" = "dnf" ]; then
        sudo dnf install -y python3
    elif [ "$PKG_MGR" = "pacman" ]; then
        sudo pacman -Sy --noconfirm python
    fi
}

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ver="$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')"
        if version_gte "$ver" "$MIN_PYTHON"; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    install_python
    for cmd in python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" >/dev/null 2>&1; then
            PYTHON_CMD="$cmd"
            break
        fi
    done
fi

[ -z "$PYTHON_CMD" ] && fail "Could not find or install Python ${MIN_PYTHON}+"
PY_VER="$("$PYTHON_CMD" --version 2>&1)"
success "Found $PY_VER"

# ── Step 3: Check/install Node ───────────────────────────────

progress "Checking Node ${MIN_NODE}+..."

install_node() {
    info "Installing Node.js..."
    if [ "$OS_TYPE" = "macos" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            printf "\n  Homebrew is required to install Node.js on macOS but is not installed.\n"
            printf "  Install Homebrew? (https://brew.sh) [y/N] "
            read -r REPLY
            if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
            else
                fail "Homebrew is required to install Node.js on macOS. Install it manually from https://brew.sh or install Node ${MIN_NODE}+ yourself."
            fi
        fi
        brew install node
    elif [ "$PKG_MGR" = "apt" ]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y -qq nodejs
    elif [ "$PKG_MGR" = "dnf" ]; then
        curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
        sudo dnf install -y nodejs
    elif [ "$PKG_MGR" = "pacman" ]; then
        sudo pacman -Sy --noconfirm nodejs npm
    fi
}

if command -v node >/dev/null 2>&1; then
    NODE_VER="$(node --version | grep -oE '[0-9]+' | head -1)"
    if [ "$NODE_VER" -ge "$MIN_NODE" ]; then
        success "Found Node $(node --version)"
    else
        install_node
        success "Installed Node $(node --version)"
    fi
else
    install_node
    command -v node >/dev/null 2>&1 || fail "Could not install Node.js"
    success "Installed Node $(node --version)"
fi

# ── Step 4: Clone/update repo ────────────────────────────────

progress "Setting up Flint at ${FLINT_HOME}..."

if [ -d "$FLINT_HOME/.git" ]; then
    info "Updating existing installation..."
    git -C "$FLINT_HOME" pull --ff-only 2>/dev/null || git -C "$FLINT_HOME" pull
    success "Updated"
else
    git clone "$REPO_URL" "$FLINT_HOME"
    success "Cloned"
fi

# ── Step 5: Create venv and install ──────────────────────────

progress "Installing Python dependencies..."

VENV_DIR="$FLINT_HOME/.venv"

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -e "$FLINT_HOME" --quiet
success "Installed flint package"

# ── Step 6: Build UI ─────────────────────────────────────────

progress "Building UI..."

cd "$FLINT_HOME/ui"
if [ ! -d "node_modules" ]; then
    npm install --silent 2>/dev/null
fi
npm run build --silent 2>/dev/null
cd "$FLINT_HOME"
success "UI built"

# ── Step 7: Start server ─────────────────────────────────────

progress "Starting Flint..."

PORT_PID="$(lsof -ti:8000 2>/dev/null || true)"
if [ -n "$PORT_PID" ]; then
    PORT_PROCESS="$(ps -p "$PORT_PID" -o comm= 2>/dev/null || echo "unknown")"
    printf "\n${RED}Port 8000 is already in use${RESET} by process %s (PID %s).\n" "$PORT_PROCESS" "$PORT_PID"
    printf "  ${DIM}Option 1:${RESET} Stop the process yourself:  kill %s\n" "$PORT_PID"
    printf "  ${DIM}Option 2:${RESET} Use a different port:      FLINT_PORT=8001 $VENV_DIR/bin/flint serve\n"
    fail "Cannot start Flint while port 8000 is occupied."
fi

"$VENV_DIR/bin/flint" serve > "$FLINT_HOME/flint.log" 2>&1 &
FLINT_PID=$!

for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    success "Running at http://localhost:8000 (PID: $FLINT_PID)"
else
    fail "Server failed to start. Check $FLINT_HOME/flint.log"
fi

if [ "$OS_TYPE" = "macos" ]; then
    open "http://localhost:8000"
elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:8000"
fi

printf "\n${GREEN}Flint is ready!${RESET}\n"
printf "  ${DIM}Dashboard:${RESET}  http://localhost:8000\n"
printf "  ${DIM}Logs:${RESET}       $FLINT_HOME/flint.log\n"
printf "  ${DIM}Stop:${RESET}       kill $FLINT_PID\n"
printf "  ${DIM}Restart:${RESET}    $VENV_DIR/bin/flint serve\n"
printf "  ${DIM}Uninstall:${RESET}  rm -rf $FLINT_HOME\n"
