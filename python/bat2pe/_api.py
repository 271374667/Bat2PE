from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterable, TypeAlias

from ._errors import BuildError, InspectError, VerifyError, map_native_error
from ._models import BuildResult, InspectResult, VerifyResult

Pathish: TypeAlias = str | Path


def _load_native():
    try:
        return importlib.import_module("bat2pe._native")
    except ImportError as exc:  # pragma: no cover - depends on local build state
        raise RuntimeError(
            "bat2pe._native is not available. Build it with `uv run maturin develop`."
        ) from exc


def _normalize_path(value: Pathish) -> str:
    return str(Path(value))


def _candidate_stub_paths(binary_name: str) -> list[Path]:
    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parents[1]
    return [
        package_dir / "bin" / f"{binary_name}.exe",
        repo_root / "target" / "debug" / f"{binary_name}.exe",
        repo_root / "target" / "release" / f"{binary_name}.exe",
    ]


def _find_stub(binary_name: str) -> str | None:
    for candidate in _candidate_stub_paths(binary_name):
        if candidate.exists():
            return str(candidate)
    return None


class Builder:
    """Build a bat2pe executable from a batch script."""

    def __init__(
        self,
        *,
        input_script: Pathish,
        output_exe: Pathish,
        window: str = "visible",
        icon: Pathish | None = None,
        company: str | None = None,
        product: str | None = None,
        description: str | None = None,
        file_version: str | None = None,
        product_version: str | None = None,
        original_filename: str | None = None,
        internal_name: str | None = None,
        stub_console: Pathish | None = None,
        stub_windows: Pathish | None = None,
    ) -> None:
        self.input_script = Path(input_script)
        self.output_exe = Path(output_exe)
        self.window = window
        self.icon = Path(icon) if icon is not None else None
        self.company = company
        self.product = product
        self.description = description
        self.file_version = file_version
        self.product_version = product_version
        self.original_filename = original_filename
        self.internal_name = internal_name
        self.stub_console = Path(stub_console) if stub_console is not None else None
        self.stub_windows = Path(stub_windows) if stub_windows is not None else None

    def build(self) -> BuildResult:
        native = _load_native()
        try:
            payload = native.build(
                _normalize_path(self.input_script),
                _normalize_path(self.output_exe),
                window=self.window,
                icon=_normalize_path(self.icon) if self.icon is not None else None,
                company=self.company,
                product=self.product,
                description=self.description,
                file_version=self.file_version,
                product_version=self.product_version,
                original_filename=self.original_filename,
                internal_name=self.internal_name,
                stub_console=_normalize_path(self.stub_console)
                if self.stub_console is not None
                else _find_stub("bat2pe-stub-console"),
                stub_windows=_normalize_path(self.stub_windows)
                if self.stub_windows is not None
                else _find_stub("bat2pe-stub-windows"),
            )
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, BuildError) from exc
        return BuildResult.from_dict(json.loads(payload))


class Inspector:
    """Inspect a bat2pe-generated executable."""

    def __init__(self, executable: Pathish) -> None:
        self.executable = Path(executable)

    def inspect(self) -> InspectResult:
        native = _load_native()
        try:
            payload = native.inspect(_normalize_path(self.executable))
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, InspectError) from exc
        return InspectResult.from_dict(json.loads(payload))


class Verifier:
    """Compare the original script and generated executable."""

    def __init__(
        self,
        script: Pathish,
        executable: Pathish,
        *,
        args: Iterable[str] | None = None,
        cwd: Pathish | None = None,
    ) -> None:
        self.script = Path(script)
        self.executable = Path(executable)
        self.args = list(args or [])
        self.cwd = Path(cwd) if cwd is not None else None

    def verify(self) -> VerifyResult:
        native = _load_native()
        try:
            payload = native.verify_pair(
                _normalize_path(self.script),
                _normalize_path(self.executable),
                args=self.args,
                cwd=_normalize_path(self.cwd) if self.cwd is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, VerifyError) from exc
        return VerifyResult.from_dict(json.loads(payload))


def build(
    *,
    input_script: Pathish,
    output_exe: Pathish,
    window: str = "visible",
    icon: Pathish | None = None,
    company: str | None = None,
    product: str | None = None,
    description: str | None = None,
    file_version: str | None = None,
    product_version: str | None = None,
    original_filename: str | None = None,
    internal_name: str | None = None,
    stub_console: Pathish | None = None,
    stub_windows: Pathish | None = None,
) -> BuildResult:
    """Build an executable with a functional top-level API."""

    return Builder(
        input_script=input_script,
        output_exe=output_exe,
        window=window,
        icon=icon,
        company=company,
        product=product,
        description=description,
        file_version=file_version,
        product_version=product_version,
        original_filename=original_filename,
        internal_name=internal_name,
        stub_console=stub_console,
        stub_windows=stub_windows,
    ).build()


def inspect(executable: Pathish) -> InspectResult:
    """Inspect a generated executable with a functional top-level API."""

    return Inspector(executable).inspect()


def verify(
    script: Pathish,
    executable: Pathish,
    *,
    args: Iterable[str] | None = None,
    cwd: Pathish | None = None,
) -> VerifyResult:
    """Verify a generated executable with a functional top-level API."""

    return Verifier(script, executable, args=args, cwd=cwd).verify()
