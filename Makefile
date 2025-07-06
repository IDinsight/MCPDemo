#!make

.PHONY: clean-docker clean-docker-container down-dev down-local help up-dev up-local

# Put it first so that "make" without argument is like "make help".
help: ## Display available commands
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-20s\033[0m %s\n", $$1, $$2}'

########## COLOR CODES FOR OUTPUT ##########
RED := $(shell tput setaf 1)
GREEN := $(shell tput setaf 2)
YELLOW := $(shell tput setaf 3)
BLUE := $(shell tput setaf 4)
RESET := $(shell tput sgr0)

########## GLOBALS ##########
SHELL := /bin/bash
PROJECT_NAME = MCPDemo

########## ENVIRONMENT VARIABLES FROM .env ##########
ifneq (,$(wildcard .env))
  include .env
  export
endif

########## DEV SETUP ##########
up-local: up-litellm up-pgvector up-redis ## Set up the local development environment by starting all containers

up-litellm: ## Set up LiteLLM container
	$(call clean-docker-container,litellm-proxy)
	@sleep 2
	@echo "$(GREEN)Starting a new LiteLLM container...$(RESET)"
	@docker run \
		--name litellm-proxy-local \
		--rm \
		-v "$(CURDIR)/cicd/litellm/litellm_config.yaml":/app/config.yaml \
		--env-file "$(CURDIR)/cicd/litellm/.env" \
		-p 4000:4000 \
		-d $(DOCKER_LITELLM_IMAGE) \
		--config /app/config.yaml --detailed_debug --telemetry False

up-pgvector: ## Set up pg-vector container
	$(call clean-docker-container,pg-vector-local)
	@sleep 2
	@echo "$(GREEN)Starting a new pg-vector container...$(RESET)"
	@docker run \
		--name pg-vector-local \
		--env-file "$(CURDIR)/backend/.env" \
		-p 5432:5432 \
		-v pgvector_data:/var/lib/postgresql/data \
		-d $(DOCKER_PG_VECTOR_IMAGE)
	@set -a && source "$(CURDIR)/backend/.env" && set +a && cd backend && python -m alembic upgrade head

up-redis: ## Set up Redis container
	$(call clean-docker-container,redis-local)
	@sleep 2
	@echo "$(GREEN)Starting a new Redis container...$(RESET)"
	@docker run \
		--name redis-local \
     	-p 6379:6379 \
	 	-d $(DOCKER_REDIS_IMAGE)


########## DEV TEARDOWN ##########
down-local: down-litellm down-pgvector down-redis ## Tear down all local development containers

down-litellm: ## Tear down LiteLLM container
	$(call clean-docker-container,litellm-proxy-local)

down-pgvector: ## Tear down pg-vector container
	$(call clean-docker-container,pg-vector-local)

down-redis: ## Tear down Redis container
	$(call clean-docker-container,redis-local)

########## DOCKER ##########
clean-docker: ## Clean up Docker volumes and networks
	@echo "$(RED)Cleaning up Docker volumes and networks...$(RESET)"
	@docker volume prune -f
	@docker network prune -f

clean-docker-container = \
	@echo "$(RED)Stopping and removing container: $(1)...$(RESET)"; \
	docker stop $(1) || true; \
	docker rm $(1) || true; \
	docker system prune -f

# Dev
up-dev: ## Set up the development environment by starting all containers using Docker compose
	@echo "$(RED)Spinning down any existing dev Docker containers...$(RESET)"
	@docker compose -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-dev.yml -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-mcp.yml -p mcp_demo-dev down
	@echo "$(GREEN)Spinning up dev Docker containers...$(RESET)"
	@docker compose -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-dev.yml -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-mcp.yml -p mcp_demo-dev up --build -d --remove-orphans
	@docker system prune -f

down-dev: ## Tear down all development containers using Docker compose
	@echo "$(RED)Spinning down dev Docker containers...$(RESET)"
	@docker compose -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-dev.yml -f ${CURDIR}/cicd/deployment/docker-compose/docker-compose-mcp.yml -p mcp_demo-dev down
