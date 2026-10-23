"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    mes_url: str = "http://127.0.0.1:8081/api/v1/trace-events"
    mock_mes_host: str = "127.0.0.1"
    mock_mes_port: int = 8081
    outbox_interval_seconds: float = 1.0
    outbox_timeout_seconds: float = 4.0
    outbox_max_attempts: int = 8
    outbox_batch_size: int = 20

    @property
    def edge_db_path(self) -> Path:
        return self.data_dir / "tracegate.db"

    @property
    def mock_mes_db_path(self) -> Path:
        return self.data_dir / "mock-mes.db"

    @classmethod
    def from_env(cls) -> "Settings":
        mock_mes_port = int(os.environ.get("TRACEGATE_MOCK_MES_PORT", "8081"))
        return cls(
            data_dir=Path(os.environ.get("TRACEGATE_DATA_DIR", "./data")).resolve(),
            host=os.environ.get("TRACEGATE_HOST", "0.0.0.0"),
            port=int(os.environ.get("TRACEGATE_PORT", "8080")),
            mes_url=os.environ.get(
                "TRACEGATE_MES_URL",
                f"http://127.0.0.1:{mock_mes_port}/api/v1/trace-events",
            ),
            mock_mes_host=os.environ.get("TRACEGATE_MOCK_MES_HOST", "127.0.0.1"),
            mock_mes_port=mock_mes_port,
            outbox_interval_seconds=float(
                os.environ.get("TRACEGATE_OUTBOX_INTERVAL", "1")
            ),
            outbox_timeout_seconds=float(
                os.environ.get("TRACEGATE_OUTBOX_TIMEOUT", "4")
            ),
            outbox_max_attempts=int(
                os.environ.get("TRACEGATE_OUTBOX_MAX_ATTEMPTS", "8")
            ),
            outbox_batch_size=int(os.environ.get("TRACEGATE_OUTBOX_BATCH", "20")),
        )
