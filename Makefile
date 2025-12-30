SHELL := /bin/zsh

UV ?= uv

PORT ?= 8000
BIND ?= 127.0.0.1

.PHONY: help sync serve stop test format coverage check-i18n setup-env local-hosts

help: ## Show available make targets
	@awk 'BEGIN {FS=":.*##"; printf "\nTargets:\n"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

sync: ## Install/update deps (uv)
	$(UV) sync --extra dev

serve: ## Start server
	$(MAKE) stop
	$(UV) run thunder-forge serve

stop: ## Stop server on PORT
	@pids=$$(lsof -ti tcp:$(PORT) 2>/dev/null || true); \
	if [[ -n "$$pids" ]]; then \
		echo "Stopping processes on port $(PORT): $$pids"; \
		kill $$pids 2>/dev/null || true; \
		sleep 0.2; \
		kill -9 $$pids 2>/dev/null || true; \
	else \
		echo "No process on port $(PORT)"; \
	fi

test: ## Run tests
	$(UV) run pytest -q

format: ## Format
	$(UV) run ruff format .

coverage: ## Run tests with coverage
	$(UV) run pytest --cov

check-i18n: ## Validate i18n file
	$(UV) run python -c "import json; from pathlib import Path; p=Path('src/static/mini_app/translations.json'); data=json.loads(p.read_text(encoding='utf-8')); assert isinstance(data, dict); [(_ for _ in ()).throw(AssertionError()) for k,v in data.items() if not (isinstance(k,str) and k and isinstance(v,str))]; print('ok')"

# Configure fabric networking from tf.yml
setup-env: ## Configure fabric IPs on nodes
	@echo
	@echo "$(UV) run python scripts/setup_env.py fabricnet"
	@echo
	@$(UV) run python scripts/setup_env.py fabricnet

# Update the hub's /etc/hosts (after setup-env succeeds)
local-hosts: ## Update local /etc/hosts with *-mgmt/*-fabric entries
	$(UV) run python scripts/setup_env.py local-hosts
