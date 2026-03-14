#!/usr/bin/env bash
set -euo pipefail

# Thunder Forge — Node Bootstrap Script
# Usage:
#   bash setup-node.sh inference   # Mac Studio inference node
#   bash setup-node.sh infra       # Radxa ROCK infrastructure node

ROLE="${1:-}"

if [[ -z "$ROLE" ]]; then
    echo "Usage: $0 <inference|infra>"
    exit 1
fi

echo "=== Thunder Forge Node Bootstrap ==="
echo "Role: $ROLE"
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
    mkdir -p ~/logs

    echo ""
    echo "=== Inference node setup complete ==="
    echo "  Homebrew: $(brew --version | head -1)"
    echo "  uv:       $(uv --version)"
    echo "  vllm-mlx: $(vllm-mlx --version 2>/dev/null || echo 'installed')"
    echo "  Logs:     ~/logs"
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
        # Add to shell profile
        if [[ -f ~/.zshrc ]]; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
        else
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
        fi
    else
        echo "uv already installed"
    fi

    # 3. Clone thunder-forge
    if [[ ! -d ~/thunder-forge ]]; then
        echo "Cloning thunder-forge..."
        git clone https://github.com/shared-goals/thunder-forge.git ~/thunder-forge
    else
        echo "thunder-forge already cloned"
        cd ~/thunder-forge && git pull
    fi

    # 4. Install dependencies
    cd ~/thunder-forge
    echo "Installing Python dependencies..."
    uv sync

    # 5. Generate docker/.env with random secrets
    if [[ ! -f ~/thunder-forge/docker/.env ]]; then
        echo "Generating docker/.env with random secrets..."
        cat > ~/thunder-forge/docker/.env <<ENVEOF
LITELLM_MASTER_KEY=sk-$(openssl rand -hex 16)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
UI_USERNAME=admin
UI_PASSWORD=$(openssl rand -hex 8)
WEBUI_SECRET_KEY=$(openssl rand -hex 16)
WEBUI_AUTH=true
ENABLE_SIGNUP=true
ENVEOF
        echo "  Save these credentials! See ~/thunder-forge/docker/.env"
    else
        echo "docker/.env already exists"
    fi

    # 6. Start Docker Compose
    echo "Starting Docker Compose stack..."
    cd ~/thunder-forge/docker
    docker compose up -d

    # 7. Generate SSH key
    if [[ ! -f ~/.ssh/id_ed25519 ]]; then
        echo "Generating SSH key..."
        ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
    else
        echo "SSH key already exists"
    fi

    echo ""
    echo "=== Infra node setup complete ==="
    echo "  Docker:  $(docker --version)"
    echo "  uv:      $(uv --version)"
    echo "  Compose: running (check: docker compose ps)"
    echo ""
    echo "Next steps:"
    echo "  1. Copy SSH public key to inference nodes:"
    echo "     for ip in 192.168.1.{101,102,103,104}; do"
    echo "       ssh-copy-id -i ~/.ssh/id_ed25519 admin@\$ip"
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
