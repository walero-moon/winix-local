PYTHON ?= python3

.PHONY: validate format-check typecheck build test compose-check

validate: format-check typecheck test compose-check

format-check:
	$(PYTHON) -m black --check src

typecheck:
	$(PYTHON) -m mypy

build:
	$(PYTHON) -m winix_local.build --project-root .

test: build
	$(PYTHON) -B -m unittest discover -s src/tests -t src -p 'test_*.py'

compose-check:
	@if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then \
		docker compose --env-file examples/mosquitto/.env.example -f examples/mosquitto/compose.yaml config --quiet; \
	else \
		echo 'Docker Compose unavailable; skipped example configuration check'; \
	fi
