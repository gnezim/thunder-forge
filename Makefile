COMPOSE = docker compose -f docker/docker-compose.yml --env-file .env

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  up              Build and start all services"
	@echo "  down            Stop all services"
	@echo "  restart         Stop and restart all services"
	@echo "  ps              Show service status"
	@echo "  logs            Show logs (optional: s=<service>)"
	@echo "  setup-gateway   Bootstrap this machine as gateway node"
	@echo "  setup-node      Bootstrap this machine as compute node"
	@echo "  check           Verify gateway setup and service health"
	@echo "  check-docker    Test Docker network connectivity to PyPI"

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) down && $(COMPOSE) up -d --build

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=50 $(s)

setup-gateway:
	zsh scripts/setup-node.sh gateway

setup-node:
	zsh scripts/setup-node.sh node

check:
	zsh scripts/setup-node.sh gateway --check

check-docker:
	@echo "==> Testing DNS resolution..."
	@docker run --rm python:3.12-slim python -c "import socket; ip = socket.getaddrinfo('pypi.org', 443)[0][4][0]; print(f'  pypi.org -> {ip}')" || echo "  FAIL: DNS resolution failed"
	@echo "==> Testing HTTPS connectivity to PyPI..."
	@docker run --rm python:3.12-slim pip install --dry-run hatchling 2>&1 | tail -5
	@echo "==> Done. If DNS or HTTPS failed, check Docker DNS config (daemon.json) or firewall/VPN settings."

.PHONY: help up down restart ps logs setup-gateway setup-node check check-docker
.DEFAULT_GOAL := help
