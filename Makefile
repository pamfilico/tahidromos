# tahidromos — development mail server
.DEFAULT_GOAL := help
COMPOSE := docker compose
DEV     := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
PY      := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: help up dev down clean logs ps restart venv test test-fast smoke reply seed user accounts open run

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "};{printf "\033[36m%-12s\033[0m %s\n",$$1,$$2}'

up: ## Start from the published image
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory open

dev: ## Build from this checkout and start
	$(DEV) up -d --build
	@$(MAKE) --no-print-directory open

run: ## Run in the foreground without Docker
	TAHIDROMOS_DATA=.run TAHIDROMOS_CONFIG_DIR=tahidromos.d \
	SMTP_PORT=2025 SUBMISSION_PORT=2587 SUBMISSION_TLS_PORT=2465 \
	IMAP_PORT=2143 IMAPS_PORT=2993 HTTP_PORT=8081 $(PY) -m tahidromos

down: ## Stop (mail is kept)
	$(COMPOSE) down

clean: ## Stop and delete every mailbox and message
	$(COMPOSE) down -v

restart: ## Restart after editing tahidromos.d/*.yml
	$(DEV) up -d --force-recreate

ps: ## Show status
	@$(COMPOSE) ps

logs: ## Follow the log
	$(COMPOSE) logs -f

venv: ## Create the test virtualenv
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt -r tests/requirements.txt
	@echo "ready: .venv"

test: ## Run the whole suite against the running server
	$(PY) -m pytest

test-fast: ## Everything except the slow bot tests
	$(PY) -m pytest -m "not slow"

smoke: ## Send one mail and prove it arrived
	$(PY) examples/send_email.py

reply: ## Walk send -> reply -> reply-to-the-reply
	$(PY) examples/reply_conversation.py --turns 6

seed: ## Fill it with realistic conversations
	$(PY) examples/seed_demo.py --bot

user: ## Create a mailbox: make user EMAIL=dana
	@test -n "$(EMAIL)" || { echo "usage: make user EMAIL=dana"; exit 1; }
	@curl -fsS -X POST http://localhost:8080/accounts \
	  -H 'content-type: application/json' -d '{"address":"$(EMAIL)"}' && echo

accounts: ## List mailboxes
	@curl -fsS http://localhost:8080/overview | $(PY) -m json.tool

open: ## Print the endpoints
	@echo ""
	@echo "  📮 tahidromos is up"
	@echo "     Inbox + API   http://localhost:8080"
	@echo "     SMTP          localhost:1025 (no auth)   submission localhost:1587"
	@echo "     IMAP          localhost:1143             IMAPS      localhost:1993"
	@echo "     Mailboxes     alice@tahidromos.test … / password"
	@echo ""
