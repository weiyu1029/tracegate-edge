"""Typed errors shared by the domain and HTTP layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DomainError(Exception):
    """A safe, structured error suitable for returning to an API client."""

    code: str
    message: str
    status: int = 400
    details: dict[str, Any] = field(default_factory=dict)
    idempotent_replay: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def as_dict(self, *, replayed: bool | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }
        include_replay = self.idempotent_replay if replayed is None else replayed
        if include_replay:
            body["idempotent_replay"] = True
        return body
