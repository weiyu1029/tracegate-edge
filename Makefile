PYTHON ?= python3
COMPOSE ?= docker compose
DOTNET ?= dotnet
DOTNET_LAB := labs/dotnet-mes

.PHONY: help run mock-mes demo test compile scan check check-dotnet check-all dotnet-run dotnet-test smoke journey resilience compose-up compose-down compose-logs

help:
	@echo "TraceGate developer commands"
	@echo "  make run           Start the edge service"
	@echo "  make mock-mes      Start the independent mock MES"
	@echo "  make demo          Start both services in one process"
	@echo "  make check         Verify the Python edge runtime"
	@echo "  make check-dotnet  Build and test the .NET MES lab"
	@echo "  make check-all     Verify both components"
	@echo "  make dotnet-run    Start the .NET MES lab"
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

dotnet-run:
	cd $(DOTNET_LAB) && $(DOTNET) run --project src/TraceGate.MesLab

dotnet-test:
	cd $(DOTNET_LAB) && $(DOTNET) build src/TraceGate.MesLab
	cd $(DOTNET_LAB) && $(PYTHON) -m unittest discover -s tests -v

check-dotnet: dotnet-test
	node --check $(DOTNET_LAB)/src/TraceGate.MesLab/wwwroot/app.js

check-all: check check-dotnet

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
