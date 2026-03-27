#!/bin/sh
set -eu

# Thunder Forge — Node Bootstrap Script
# Usage:
#   zsh setup-node.sh node              # Mac Studio compute node (macOS)
#   bash setup-node.sh gateway          # Gateway node (Linux)
#   setup-node.sh node --check          # Verify setup without installing
#   setup-node.sh gateway --check       # Verify gateway setup

ROLE="${1:-}"
CHECK_ONLY="${2:-}"

if [ -z "$ROLE" ] || { [ "$ROLE" != "node" ] && [ "$ROLE" != "gateway" ]; }; then
    echo "Usage: $0 <node|gateway> [--check]"
    exit 1
fi

# ── Load .env ─────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
for envfile in "$SCRIPT_DIR/../.env" "$SCRIPT_DIR/.env" "$HOME/.thunder-forge.env"; do
    if [ -f "$envfile" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            # Strip inline comments and whitespace
            line="${line%%#*}"
            line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            [ -z "$line" ] && continue
            case "$line" in
                *=*)
                    key="${line%%=*}"
                    value="${line#*=}"
                    # Strip surrounding quotes
                    case "$value" in
                        \"*\") value="${value#\"}"; value="${value%\"}" ;;
                        \'*\') value="${value#\'}"; value="${value%\'}" ;;
                    esac
                    # Expand leading tilde
                    case "$value" in
                        "~"*) value="$HOME${value#\~}" ;;
                    esac
                    # Only set if not already in environment
                    eval "current=\${$key:-}"
                    [ -z "$current" ] && export "$key=$value"
                    ;;
                *)
                    echo "Warning: cannot parse .env line: $line"
                    ;;
            esac
        done < "$envfile"
        echo "Loaded config from $envfile"
    fi
done

# ── Configurable paths ────────────────────────────────
TF_DIR="${TF_DIR:-$HOME/thunder-forge}"
case "$TF_DIR" in "~"*) TF_DIR="$HOME${TF_DIR#\~}" ;; esac
TF_LOG_DIR="${TF_LOG_DIR:-$HOME/logs}"
case "$TF_LOG_DIR" in "~"*) TF_LOG_DIR="$HOME${TF_LOG_DIR#\~}" ;; esac
GATEWAY_SSH_KEY="${GATEWAY_SSH_KEY:-$HOME/.ssh/id_ed25519}"
case "$GATEWAY_SSH_KEY" in "~"*) GATEWAY_SSH_KEY="$HOME${GATEWAY_SSH_KEY#\~}" ;; esac
TF_REPO_URL="${TF_REPO_URL:-https://github.com/shared-goals/thunder-forge.git}"

# ── Helpers ───────────────────────────────────────────
STEP_NUM=0
TOTAL_STEPS=0

step() {
    STEP_NUM=$((STEP_NUM + 1))
    echo ""
    echo "[$STEP_NUM/$TOTAL_STEPS] $1"
}

ok()   { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; }
warn() { echo "  ! $1"; }

append_if_missing() {
    line="$1"; shift
    for f in "$@"; do
        grep -qF "$line" "$f" 2>/dev/null || echo "$line" >> "$f"
    done
}

# ── Pre-checks ────────────────────────────────────────
preflight() {
    echo "Checking prerequisites..."
    errors=0

    if [ "$(id -u)" = "0" ]; then
        fail "Running as root — run as a regular user instead"
        errors=$((errors + 1))
    else
        ok "Running as $(whoami)"
    fi

    if command -v curl >/dev/null 2>&1; then
        ok "curl available"
    else
        fail "curl not found — install: xcode-select --install (macOS) or apt install curl (Linux)"
        errors=$((errors + 1))
    fi

    if curl -sI --connect-timeout 5 https://github.com >/dev/null 2>&1; then
        ok "Internet reachable"
    else
        fail "Cannot reach github.com — check network/proxy"
        errors=$((errors + 1))
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "Fix the issues above and retry."
        exit 1
    fi

    # Prompt for sudo upfront (needed for pmset on macOS, usermod on Linux)
    echo ""
    echo "Some steps need sudo (sleep disable, Docker group)."
    echo "Enter your password now if prompted:"
    sudo -v || true
}

# ── Verify functions (used by --check and after setup) ─
verify_node() {
    echo ""
    echo "Verifying node setup..."
    errors=0

    if command -v brew >/dev/null 2>&1; then
        ok "brew $(brew --version 2>/dev/null | head -1) at $(command -v brew)"
    else
        fail "Homebrew not found"
        errors=$((errors + 1))
    fi

    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null) at $(command -v uv)"
    else
        fail "uv not found"
        errors=$((errors + 1))
    fi

    if command -v vllm-mlx >/dev/null 2>&1; then
        ok "vllm-mlx installed"
    else
        fail "vllm-mlx not found"
        errors=$((errors + 1))
    fi

    if [ -d "$TF_LOG_DIR" ]; then
        ok "Log directory: $TF_LOG_DIR"
    else
        fail "Log directory missing: $TF_LOG_DIR"
        errors=$((errors + 1))
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "$errors issues found."
        return 1
    else
        echo ""
        echo "Node setup verified — all OK."
        return 0
    fi
}

verify_gateway() {
    echo ""
    echo "Verifying gateway setup..."
    errors=0

    if command -v docker >/dev/null 2>&1; then
        ok "docker $(docker --version 2>/dev/null)"
    else
        fail "Docker not found"
        errors=$((errors + 1))
    fi

    if command -v uv >/dev/null 2>&1; then
        ok "uv $(uv --version 2>/dev/null)"
    else
        fail "uv not found"
        errors=$((errors + 1))
    fi

    if command -v hf >/dev/null 2>&1; then
        ok "hf CLI installed"
        if hf auth whoami >/dev/null 2>&1; then
            ok "HuggingFace authenticated"
        else
            warn "HuggingFace not authenticated — run: hf auth login"
        fi
    else
        fail "hf CLI not found"
        errors=$((errors + 1))
    fi

    if [ -f "$TF_DIR/pyproject.toml" ]; then
        ok "thunder-forge cloned at $TF_DIR"
    else
        fail "thunder-forge not found at $TF_DIR"
        errors=$((errors + 1))
    fi

    # Check Docker Compose services
    if [ -f "$TF_DIR/docker/docker-compose.yml" ] || [ -f "$TF_DIR/docker/compose.yaml" ]; then
        cd "$TF_DIR"
        running=$(docker compose -f docker/docker-compose.yml ps --format '{{.Name}} {{.State}}' 2>/dev/null || true)
        if echo "$running" | grep -q "running"; then
            ok "Docker Compose services running"
        else
            fail "Docker Compose services not running — run: cd $TF_DIR && docker compose -f docker/docker-compose.yml up -d"
            errors=$((errors + 1))
        fi
    fi

    if [ "$errors" -gt 0 ]; then
        echo ""
        echo "$errors issues found."
        return 1
    else
        echo ""
        echo "Gateway setup verified — all OK."
        return 0
    fi
}

# ── --check mode ──────────────────────────────────────
if [ "$CHECK_ONLY" = "--check" ]; then
    case "$ROLE" in
        node)    verify_node; exit $? ;;
        gateway) verify_gateway; exit $? ;;
    esac
fi

# ── Setup functions ───────────────────────────────────
setup_node() {
    TOTAL_STEPS=6
    echo "=== Thunder Forge Node Setup ==="
    echo "TF_DIR=$TF_DIR"
    echo ""
    preflight

    step "Installing Homebrew..."
    if command -v brew >/dev/null 2>&1; then
        ok "Already installed"
    else
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        append_if_missing 'eval "$(/opt/homebrew/bin/brew shellenv)"' ~/.zshenv ~/.zshrc
        eval "$(/opt/homebrew/bin/brew shellenv)"
        ok "Installed"
    fi

    step "Installing uv..."
    if command -v uv >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
        ok "Installed"
    fi

    step "Installing vllm-mlx..."
    if command -v vllm-mlx >/dev/null 2>&1; then
        ok "Already installed"
        echo "  Upgrading..."
        uv tool upgrade --all 2>/dev/null || true
    else
        uv tool install vllm-mlx
        ok "Installed"
    fi

    step "Configuring PATH..."
    append_if_missing 'eval "$(/opt/homebrew/bin/brew shellenv)"' ~/.zshenv ~/.zshrc
    append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
    ok "~/.zshenv and ~/.zshrc updated"

    step "Disabling macOS sleep..."
    if [ "${TF_DISABLE_SLEEP:-true}" = "true" ]; then
        sudo pmset -a sleep 0 displaysleep 0 disksleep 0
        ok "Sleep disabled"
    else
        ok "Skipped (TF_DISABLE_SLEEP=false)"
    fi

    step "Creating directories..."
    mkdir -p "$TF_LOG_DIR"
    ok "Log directory: $TF_LOG_DIR"

    verify_node

    echo ""
    echo "Next: deploy from your workstation with 'uv run thunder-forge deploy'"
}

setup_gateway() {
    TOTAL_STEPS=8
    echo "=== Thunder Forge Gateway Setup ==="
    echo "TF_DIR=$TF_DIR"
    echo ""
    preflight

    step "Installing Docker..."
    if command -v docker >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        ok "Installed (log out and back in for group to take effect)"
    fi

    step "Installing uv..."
    if command -v uv >/dev/null 2>&1; then
        ok "Already installed"
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        append_if_missing 'export PATH="$HOME/.local/bin:$PATH"' ~/.zshenv ~/.zshrc
        ok "Installed"
    fi

    step "Installing HuggingFace CLI..."
    uv tool install --force huggingface_hub --with socksio
    ok "hf installed with socksio"
    if command -v hf >/dev/null 2>&1 && hf auth whoami >/dev/null 2>&1; then
        ok "HuggingFace authenticated"
    else
        warn "Not authenticated — run: hf auth login"
    fi

    step "Cloning thunder-forge..."
    if [ -d "$TF_DIR/.git" ]; then
        ok "Already cloned"
        cd "$TF_DIR" && git pull
    else
        git clone "$TF_REPO_URL" "$TF_DIR"
        ok "Cloned to $TF_DIR"
    fi

    step "Installing Python dependencies..."
    cd "$TF_DIR"
    uv sync
    uv tool upgrade --all 2>/dev/null || true
    ok "Dependencies installed"

    step "Generating secrets..."
    if [ -f "$TF_DIR/.env" ]; then
        ok ".env already exists"
    else
        cat > "$TF_DIR/.env" <<ENVEOF
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
UI_USERNAME=admin
UI_PASSWORD=$(openssl rand -hex 8)
WEBUI_SECRET_KEY=$(openssl rand -hex 16)
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENVEOF
        ok "Generated .env — save these credentials!"
    fi

    step "Starting Docker Compose..."
    cd "$TF_DIR"
    docker compose -f docker/docker-compose.yml up -d
    echo "  Waiting for services..."
    attempt=0
    max_attempts=12
    while [ "$attempt" -lt "$max_attempts" ]; do
        attempt=$((attempt + 1))
        if curl -sI http://localhost:4000/health >/dev/null 2>&1; then
            ok "LiteLLM healthy (port 4000)"
            break
        fi
        if [ "$attempt" -eq "$max_attempts" ]; then
            warn "LiteLLM not responding yet — check: docker compose -f docker/docker-compose.yml logs litellm"
        else
            sleep 5
        fi
    done

    step "SSH key..."
    if [ -f "$GATEWAY_SSH_KEY" ]; then
        ok "Key exists: $GATEWAY_SSH_KEY"
    else
        mkdir -p "$(dirname "$GATEWAY_SSH_KEY")"
        ssh-keygen -t ed25519 -f "$GATEWAY_SSH_KEY" -N ""
        ok "Generated: $GATEWAY_SSH_KEY"
    fi

    verify_gateway

    echo ""
    echo "Next steps:"
    echo "  1. Copy SSH key to each node: ssh-copy-id -i $GATEWAY_SSH_KEY <user>@<node-ip>"
    echo "  2. Run: uv run thunder-forge deploy"
}

case "$ROLE" in
    node)    setup_node ;;
    gateway) setup_gateway ;;
esac
