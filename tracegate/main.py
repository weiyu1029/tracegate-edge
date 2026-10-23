"""Command-line composition root for edge, mock-MES, and combined demo modes."""

from __future__ import annotations

import argparse
import threading
from http.server import ThreadingHTTPServer
from typing import Sequence

from .config import Settings
from .db import Database
from .http_api import create_edge_server
from .mock_mes import MockMesStore, create_mock_mes_server
from .outbox import OutboxDispatcher, OutboxWorker
from .service import TraceGateService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracegate",
        description="Run the synthetic TraceGate Edge reference system.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("edge", "mock-mes", "demo"),
        default="demo",
        help="edge only, mock receiver only, or both (default: demo)",
    )
    return parser


def make_service(settings: Settings) -> TraceGateService:
    database = Database(settings.edge_db_path)
    database.migrate(seed=True)
    return TraceGateService(database)


def make_worker(settings: Settings, service: TraceGateService) -> OutboxWorker:
    dispatcher = OutboxDispatcher(
        service.db,
        settings.mes_url,
        timeout_seconds=settings.outbox_timeout_seconds,
        max_attempts=settings.outbox_max_attempts,
        batch_size=settings.outbox_batch_size,
    )
    return OutboxWorker(
        dispatcher, interval_seconds=settings.outbox_interval_seconds
    )


def run_edge(settings: Settings) -> None:
    service = make_service(settings)
    worker = make_worker(settings, service)
    server = create_edge_server(service, settings.host, settings.port, worker=worker)
    worker.start()
    _announce_edge(settings)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TraceGate Edge...", flush=True)
    finally:
        worker.stop()
        server.server_close()


def run_mock_mes(settings: Settings) -> None:
    store = MockMesStore(settings.mock_mes_db_path)
    server = create_mock_mes_server(
        store, settings.mock_mes_host, settings.mock_mes_port
    )
    print(
        f"Synthetic mock MES: http://{settings.mock_mes_host}:{settings.mock_mes_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping synthetic mock MES...", flush=True)
    finally:
        server.server_close()


def run_demo(settings: Settings) -> None:
    store = MockMesStore(settings.mock_mes_db_path)
    mock_server = create_mock_mes_server(
        store, settings.mock_mes_host, settings.mock_mes_port
    )
    threading.Thread(
        target=mock_server.serve_forever,
        name="tracegate-mock-mes",
        daemon=True,
    ).start()
    service = make_service(settings)
    worker = make_worker(settings, service)
    edge_server = create_edge_server(
        service,
        settings.host,
        settings.port,
        worker=worker,
        demo_mes_controller=store.set_online,
        demo_reset_controller=store.reset,
        allow_demo_controls=True,
    )
    worker.start()
    _announce_edge(settings)
    print(
        f"Synthetic mock MES: http://{settings.mock_mes_host}:{settings.mock_mes_port}",
        flush=True,
    )
    print("Demo controls are enabled; all identities and records are fictional.", flush=True)
    try:
        edge_server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping TraceGate demo...", flush=True)
    finally:
        worker.stop()
        edge_server.server_close()
        mock_server.shutdown()
        mock_server.server_close()


def _announce_edge(settings: Settings) -> None:
    print(f"TraceGate Edge: http://0.0.0.0:{settings.port}", flush=True)
    print(f"Upstream:      {settings.mes_url}", flush=True)
    print(f"Data:          {settings.data_dir}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "edge":
        run_edge(settings)
    elif args.mode == "mock-mes":
        run_mock_mes(settings)
    else:
        run_demo(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
