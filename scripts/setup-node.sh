#!/usr/bin/env bash
set -euo pipefail

# Thunder Forge — Node Bootstrap Script
# Usage:
#   bash setup-node.sh inference   # Mac Studio inference node
#   bash setup-node.sh infra       # Radxa ROCK infrastructure node
#
# All paths are configurable via environment variables or a .env file:
#   TF_DIR          — thunder-forge clone location      (default: ~/thunder-forge)
#   TF_LOG_DIR      — inference node log directory      (default: ~/logs)
#   TF_SSH_KEY      — SSH key path                      (default: ~/.ssh/id_ed25519)
#   TF_REPO_URL     — git clone URL                     (default: https://github.com/shared-goals/thunder-forge.git)
#   HF_HOME         — HuggingFace cache directory       (default: ~/.cache/huggingface)
#
# Place a .env file next to this script or at ~/.thunder-forge.env

ROLE="${1:-}"

if [[ -z "$ROLE" ]]; then
    echo "Usage: $0 <inference|infra>"
    exit 1
fi

# ── Load .env (script-local first, then home dir) ─────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for envfile in "$SCRIPT_DIR/.env" "$HOME/.thunder-forge.env"; do
    if [[ -f "$envfile" ]]; then
        # Source only lines matching KEY=VALUE, skip comments and blanks.
        # Existing env vars take precedence (won't overwrite).
        while IFS='=' read -r key value; do
            [[ -z "$key" || "$key" == \#* ]] && continue
            value="${value%\"}" && value="${value#\"}"  # strip quotes
            export "${key}=${!key:-$value}"
        done < "$envfile"
        echo "Loaded config from $envfile"
    fi
done

# ── Configurable paths ────────────────────────────────
# Expand ~ to $HOME (tilde doesn't expand when read from .env files)
TF_DIR="${TF_DIR:-$HOME/thunder-forge}"
TF_DIR="${TF_DIR/#\~/$HOME}"
TF_LOG_DIR="${TF_LOG_DIR:-$HOME/logs}"
TF_LOG_DIR="${TF_LOG_DIR/#\~/$HOME}"
TF_SSH_KEY="${TF_SSH_KEY:-$HOME/.ssh/id_ed25519}"
TF_SSH_KEY="${TF_SSH_KEY/#\~/$HOME}"
TF_REPO_URL="${TF_REPO_URL:-https://github.com/shared-goals/thunder-forge.git}"

echo "=== Thunder Forge Node Bootstrap ==="
echo "Role: $ROLE"
echo "TF_DIR=$TF_DIR"
echo ""

setup_inference() {
    echo "--- Setting up inference node (macOS) ---"
    echo ""

    # 1. Homebrew
    if ! command -v brew &>/dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        echo "Homebrew already installed"
    fi

    # 2. uv
    if ! command -v uv &>/dev/null; then
        echo "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
    else
        echo "uv already installed"
    fi

    # 3. vllm-mlx
    if ! command -v vllm-mlx &>/dev/null; then
        echo "Installing vllm-mlx..."
        uv tool install vllm-mlx
    else
        echo "vllm-mlx already installed"
    fi

    # 4. Disable macOS sleep
    echo "Disabling macOS sleep..."
    sudo pmset -a sleep 0 displaysleep 0 disksleep 0

    # 5. Create logs directory
    mkdir -p "$TF_LOG_DIR"

    echo ""
    echo "=== Inference node setup complete ==="
    echo "  Homebrew: $(brew --version | head -1)"
    echo "  uv:       $(uv --version)"
    echo "  vllm-mlx: $(vllm-mlx --version 2>/dev/null || echo 'installed')"
    echo "  Logs:     $TF_LOG_DIR"
    echo ""
    echo "Next steps:"
    echo "  1. Ensure SSH key from rock is in ~/.ssh/authorized_keys"
    echo "  2. Run 'thunder-forge deploy --node <this-node>' from rock"
}

setup_infra() {
    echo "--- Setting up infra node (Linux ARM64) ---"
    echo ""

    # 1. Docker Engine
    if ! command -v docker &>/dev/null; then
        echo "Installing Docker Engine..."
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        echo "Log out and back in for docker group to take effect,"
        echo "or run: newgrp docker"
    else
        echo "Docker already installed"
    fi

    # 2. uv
    if ! command -v uv &>/dev/null; then
        echo "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
    else
        echo "uv already installed"
    fi

    # 3. hf (HuggingFace CLI)
    if ! command -v hf &>/dev/null; then
        echo "Installing HuggingFace CLI (hf)..."
        uv tool install huggingface_hub --with socksio
    else
        echo "HuggingFace CLI (hf) already installed"
    fi

    # 4. Check HuggingFace auth
    if command -v hf &>/dev/null && hf auth whoami &>/dev/null; then
        echo "HuggingFace auth: $(hf auth whoami 2>/dev/null | head -1)"
    else
        echo "WARNING: HuggingFace not authenticated. Gated models will fail to download."
        echo "  Run: hf auth login"
    fi

    # 5. Check proxy env vars
    if [[ -z "${HTTP_PROXY:-}" && -z "${HTTPS_PROXY:-}" ]]; then
        echo "WARNING: HTTP_PROXY/HTTPS_PROXY not set. Outbound downloads may fail."
    else
        echo "Proxy: ${HTTPS_PROXY:-${HTTP_PROXY}}"
    fi

    # 6. Clone thunder-forge
    if [[ ! -d "$TF_DIR" ]]; then
        echo "Cloning thunder-forge..."
        git clone "$TF_REPO_URL" "$TF_DIR"
    else
        echo "thunder-forge already cloned"
        cd "$TF_DIR" && git pull
    fi

    # 7. Install dependencies
    cd "$TF_DIR"
    echo "Installing Python dependencies..."
    uv sync

    # 8. Generate docker/.env with random secrets
    if [[ ! -f "$TF_DIR/docker/.env" ]]; then
        echo "Generating docker/.env with random secrets..."
        cat > "$TF_DIR/docker/.env" <<ENVEOF
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
UI_USERNAME=admin
UI_PASSWORD=$(openssl rand -hex 8)
WEBUI_SECRET_KEY=$(openssl rand -hex 16)
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENVEOF
        echo "  Save these credentials! See $TF_DIR/docker/.env"
    else
        echo "docker/.env already exists"
    fi

    # 9. Start Docker Compose
    echo "Starting Docker Compose stack..."
    cd "$TF_DIR/docker"
    docker compose up -d

    # 10. Generate SSH key
    if [[ -f "$TF_SSH_KEY" ]]; then
        echo "SSH key already exists: $TF_SSH_KEY"
    else
        mkdir -p "$(dirname "$TF_SSH_KEY")"
        echo "Generating SSH key..."
        ssh-keygen -t ed25519 -f "$TF_SSH_KEY" -N ""
    fi

    echo ""
    echo "=== Infra node setup complete ==="
    echo "  Docker:       $(docker --version)"
    echo "  uv:           $(uv --version)"
    echo "  hf:           $(hf version 2>/dev/null || echo 'not installed')"
    echo "  Compose:      running (check: docker compose ps)"
    echo ""
    echo "Next steps:"
    echo "  1. Copy SSH public key to inference nodes:"
    echo "     for ip in 192.168.1.{101,102,103,104}; do"
    echo "       ssh-copy-id -i $TF_SSH_KEY \$USER@\$ip"
    echo "     done"
    echo "  2. Run: uv run thunder-forge ensure-models"
    echo "  3. Run: uv run thunder-forge deploy"
    echo "  4. Set up GitHub Actions runner (needs token from GitHub UI)"
}

case "$ROLE" in
    inference) setup_inference ;;
    infra)     setup_infra ;;
    *)
        echo "Unknown role: $ROLE"
        echo "Usage: $0 <inference|infra>"
        exit 1
        ;;
esac
