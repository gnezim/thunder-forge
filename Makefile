COMPOSE = docker compose -f docker/docker-compose.yml --env-file .env

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

.PHONY: up down restart ps logs setup-gateway setup-node
