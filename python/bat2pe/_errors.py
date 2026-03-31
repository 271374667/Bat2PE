from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Error code constants matching the Rust core error codes in
# crates/bat2pe-core/src/error.rs.
ERR_INVALID_INPUT = 100
ERR_UNSUPPORTED_INPUT = 101
ERR_UNSUPPORTED_ENCODING = 102
ERR_RESOURCE_NOT_FOUND = 103
ERR_INVALID_EXECUTABLE = 104
ERR_DIRECTORY_NOT_WRITABLE = 105
ERR_IO = 106
ERR_CLI_USAGE = 107
ERR_VERIFY_MISMATCH = 108
ERR_VERIFY_UAC_INTERACTIVE = 109


class Bat2PeError(Exception):
    """Base exception for Python-side bat2pe errors."""

    def __init__(
        self,
        message: str,
        *,
        code: int,
        path: str | Path | None = None,
        details: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = Path(path) if path is not None else None
        self.details = details

    def __str__(self) -> str:
        base = super().__str__()
        if self.path is not None:
            base = f"{base} ({self.path})"
        if self.details:
            base = f"{base}: {self.details}"
        return base


class BuildError(Bat2PeError):
    """Raised when building an executable fails."""


class InspectError(Bat2PeError):
    """Raised when inspecting an executable fails."""


class VerifyError(Bat2PeError):
    """Raised when verification fails before a comparison result is produced."""


def map_native_error(exc: BaseException, error_type: type[Bat2PeError]) -> Bat2PeError:
    payload = _parse_payload(str(exc))
    if payload is None:
        return error_type(str(exc), code=1)
    return error_type(
        payload.get("message", "bat2pe error"),
        code=int(payload.get("code", 1)),
        path=payload.get("path"),
        details=payload.get("details"),
    )


def _parse_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None
