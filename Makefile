PYTHON ?= python3
COMPOSE ?= docker compose

.PHONY: help run mock-mes demo test compile scan check smoke journey resilience compose-up compose-down compose-logs

help:
	@echo "TraceGate Edge developer commands"
	@echo "  make run           Start the edge service"
	@echo "  make mock-mes      Start the independent mock MES"
	@echo "  make demo          Start both services in one process"
	@echo "  make check         Compile, test, and scan release files"
	@echo "  make journey       Run the synthetic end-to-end journey"
	@echo "  make resilience    Prove outage recovery and measure reads"
	@echo "  make compose-up    Start separated containers"

run:
	$(PYTHON) -m tracegate edge

mock-mes:
	$(PYTHON) -m tracegate mock-mes

demo:
	$(PYTHON) -m tracegate demo

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

compile:
	$(PYTHON) -m compileall -q tracegate tracegate.py scripts tests

scan:
	$(PYTHON) scripts/public_release_check.py .

check: compile test scan

smoke:
	$(PYTHON) scripts/demo_smoke.py --wait 30

journey:
	$(PYTHON) scripts/demo_smoke.py --wait 30 --journey

resilience:
	$(PYTHON) scripts/demo_smoke.py --wait 30 --journey --outage-demo --benchmark 50

compose-up:
	$(COMPOSE) up --build -d

compose-down:
	$(COMPOSE) down

compose-logs:
	$(COMPOSE) logs -f --tail=100
