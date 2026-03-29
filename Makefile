.PHONY: install dev serve test build clean help

install: ## Install Python + UI dependencies
	pip install -e ".[dev]"
	cd ui && npm install

dev: ## Start API (hot reload) + UI dev server
	flint serve --dev &
	cd ui && npm run dev

serve: ## Build UI and start production server
	cd ui && npm run build
	flint serve

test: ## Run all tests
	pytest tests/ -v

build: ## Build Docker image
	docker build -t flint .

clean: ## Remove generated files
	rm -rf data/ ui/dist/ ui/node_modules/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[33m%-12s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
