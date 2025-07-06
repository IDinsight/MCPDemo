#!make

.PHONY: clean fresh-env help install lint uv-check-updates uv-lock uv-sync uv-update-deps

# Put it first so that "make" without argument is like "make help".
help: ## Display available commands
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[32m%-20s\033[0m %s\n", $$1, $$2}'

########## GLOBALS ##########
SHELL := /bin/bash
PROJECT_NAME = IdeaGenerator

# Color codes for output.
RED := $(shell tput setaf 1)
GREEN := $(shell tput setaf 2)
YELLOW := $(shell tput setaf 3)
BLUE := $(shell tput setaf 4)
RESET := $(shell tput sgr0)

########## HELPERS ##########
VENV ?= .venv
PYTHON = $(VENV)/bin/python
UV = uv
UV_PIP = $(UV) pip

########## CLEAN UP ##########
# Delete all compiled Python files.
clean: ## Remove compiled Python files, caches, and old virtual environments
	@echo "$(BLUE)Cleaning backend project files...$(RESET)"
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@rm -rf $(VENV) dist build *.egg-info .pytest_cache .coverage htmlcov
	@echo "$(GREEN)Cleanup complete.$(RESET)"

########## BACKEND ENVIRONMENT SETUP ##########
install: ## Create venv (if missing) and install ALL deps (core + [dev]).
	@echo "$(YELLOW)Creating fresh backend environment: $(PROJECT_NAME)$(RESET)"
	@$(UV) venv
	@$(PYTHON) -m ensurepip --upgrade
	@$(UV_PIP) sync ../cicd/requirements/requirements.txt ../cicd/requirements/requirements_dev.txt
	@echo "$(GREEN)Backend environment setup complete.$(RESET)"

fresh-env: clean install ## Create a fresh backend environment

########## LINTING ##########
lint: lint-isort lint-black lint-ruff lint-interrogate lint-mypy lint-pylint lint-cloc ## Run all linter and formatter checks

lint-isort: ## Sort imports
	@echo "$(BLUE)Running isort on backend...$(RESET)"
	@$(VENV)/bin/isort src/

lint-black: ## Format code with Black
	@echo "$(BLUE)Running black on backend...$(RESET)"
	@$(VENV)/bin/black src/

lint-ruff: ## Run Ruff checks
	@echo "$(BLUE)Running ruff on backend...$(RESET)"
	@$(VENV)/bin/ruff check src/

lint-interrogate: ## Run Interrogate for docstring coverage
	@echo "$(BLUE)Running interrogate on backend...$(RESET)"
	@$(VENV)/bin/interrogate -v src/

lint-mypy: ## Run MyPy for type checking
	@echo "$(BLUE)Running mypy on backend...$(RESET)"
	@$(VENV)/bin/mypy src/ --ignore-missing-imports --show-error-codes

lint-pylint: ## Run Pylint for code quality checks
	@echo "$(BLUE)Running pylint on backend...$(RESET)"
	@$(VENV)/bin/pylint src/

lint-cloc: ## Run CLOC for code statistics
	@echo "$(BLUE)Running cloc on backend...$(RESET)"
	@cloc src/

########## UV ##########
uv-check-updates: ## Show outdated packages in the current venv
	@echo "$(YELLOW)Checking for outdated packages...$(RESET)"
	@$(UV_PIP) list --outdated
	@echo "$(GREEN)Finished check!$(RESET)"

uv-lock: ## Lock and update dependencies as specified in pyproject.toml
	@echo "$(YELLOW)Locking dependencies...$(RESET)"
	@$(UV_PIP) compile pyproject.toml --output-file ../cicd/requirements/requirements.txt --generate-hashes
	@$(UV_PIP) compile pyproject.toml --extra dev --output-file ../cicd/requirements/requirements_dev.txt --generate-hashes
	@$(UV) lock
	@echo "$(GREEN)Dependencies locked.$(RESET)"

uv-sync: ## Sync venv to match current lockfile
	@echo "$(YELLOW)Syncing venv to match current lockfile...$(RESET)"
	@$(UV_PIP) sync ../cicd/requirements/requirements.txt ../cicd/requirements/requirements_dev.txt
	@echo "$(GREEN)venv synced with current lockfile!$(RESET)"

uv-update-deps: ## Update everything to the newest versions allowed by pyproject.toml
	@echo "$(YELLOW)Exporting dependencies to requirements files...$(RESET)"
	@$(UV_PIP) install -r ../cicd/requirements/requirements.txt -r ../cicd/requirements/requirements_dev.txt --upgrade
	@echo "$(GREEN)Dependencies exported.$(RESET)"
