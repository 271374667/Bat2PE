"""Object-oriented and functional Python API for bat2pe.

`Builder`, `Inspector`, and `Verifier` are the primary object-oriented entry
points. The top-level `build()`, `inspect()`, and `verify()` helpers are thin
wrappers for callers that prefer a functional style.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Iterable, TypeAlias

from ._errors import BuildError, InspectError, VerifyError, map_native_error
from ._models import BuildResult, InspectResult, VerifyResult

Pathish: TypeAlias = str | Path


def _load_native():
    """Load the compiled native extension used by the public API.

    Returns:
        module: The imported `bat2pe._native` module.

    Raises:
        RuntimeError: If the compiled extension is unavailable in the current
            environment.
    """

    try:
        return importlib.import_module("bat2pe._native")
    except ImportError as exc:  # pragma: no cover - depends on local build state
        raise RuntimeError(
            "bat2pe._native is not available. Build it with `uv run maturin develop`."
        ) from exc


def _normalize_path(value: Pathish) -> str:
    """Convert a path-like value to the string form expected by native code.

    Args:
        value: File system path supplied as a string or `pathlib.Path`.

    Returns:
        str: The normalized path string forwarded to the native extension.
    """

    return str(Path(value))


def _candidate_stub_paths(binary_name: str) -> list[Path]:
    """Build the ordered list of stub executable locations to probe.

    Args:
        binary_name: Stub executable base name without the `.exe` suffix.

    Returns:
        list[Path]: Candidate paths searched from packaged binaries to local
        development build outputs.
    """

    package_dir = Path(__file__).resolve().parent
    repo_root = package_dir.parents[1]
    return [
        package_dir / "bin" / f"{binary_name}.exe",
        repo_root / "target" / "debug" / f"{binary_name}.exe",
        repo_root / "target" / "release" / f"{binary_name}.exe",
    ]


def _find_stub(binary_name: str) -> str | None:
    """Return the first existing stub executable for a given binary name.

    Args:
        binary_name: Stub executable base name without the `.exe` suffix.

    Returns:
        str | None: Absolute path to the first discovered stub executable, or
        `None` when no candidate exists.
    """

    for candidate in _candidate_stub_paths(binary_name):
        if candidate.exists():
            return str(candidate)
    return None


class Builder:
    """Stateful builder for converting a batch script into an executable.

    This class is the object-oriented entry point for build configuration. It
    stores all build inputs on the instance so callers can prepare options once
    and invoke `build()` later.

    Examples:
        Build a hidden-window executable with version metadata:

            builder = Builder(
                input_script="scripts/hello.bat",
                output_exe="dist/hello.exe",
                window="hidden",
                company="Example Co.",
                product="Batch Tools",
                file_version="1.2.0",
            )
            result = builder.build()
    """

    def __init__(
        self,
        *,
        input_script: Pathish,
        output_exe: Pathish | None = None,
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
        """Initialize a build request.

        Args:
            input_script: Path to the source batch script to embed into the
                generated executable.
            output_exe: Optional output path for the generated executable. When
                omitted, the native builder decides the final output location.
            window: Window visibility mode forwarded to the native builder.
                Common values are `"visible"` and `"hidden"`.
            icon: Optional path to an `.ico` file embedded into the executable.
            company: Optional company name written into version metadata.
            product: Optional product name written into version metadata.
            description: Optional file description written into version
                metadata.
            file_version: Optional file version string written into version
                metadata.
            product_version: Optional product version string written into
                version metadata.
            original_filename: Optional original filename recorded in version
                metadata.
            internal_name: Optional internal name recorded in version metadata.
            stub_console: Optional path to the console-mode stub executable.
                When omitted, bat2pe attempts to discover a packaged or locally
                built stub automatically.
            stub_windows: Optional path to the windowed stub executable. When
                omitted, bat2pe attempts to discover a packaged or locally
                built stub automatically.
        """

        self.input_script = Path(input_script)
        self.output_exe = Path(output_exe) if output_exe is not None else None
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
        """Build an executable from the options stored on this instance.

        Returns:
            BuildResult: Build metadata plus an inspection snapshot of the
            generated executable.

        Raises:
            BuildError: If the native build process fails and returns a mapped
                bat2pe build error.
            RuntimeError: If the `bat2pe._native` extension is unavailable.

        Examples:
            Build immediately from a prepared builder:

                result = Builder(input_script="hello.bat").build()
        """

        native = _load_native()
        try:
            payload = native.build(
                _normalize_path(self.input_script),
                _normalize_path(self.output_exe) if self.output_exe is not None else None,
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
    """Stateful inspector for reading metadata from a generated executable.

    Use this class when inspection is part of a larger workflow and you want to
    keep the target executable on the instance before calling `inspect()`.

    Examples:
        Read embedded runtime and version information:

            inspector = Inspector("dist/hello.exe")
            result = inspector.inspect()
    """

    def __init__(self, executable: Pathish) -> None:
        """Initialize an inspector for a generated executable.

        Args:
            executable: Path to the `.exe` file produced by bat2pe and targeted
                for inspection.
        """

        self.executable = Path(executable)

    def inspect(self) -> InspectResult:
        """Inspect the configured executable and decode its embedded metadata.

        Returns:
            InspectResult: Parsed executable metadata, including runtime
            configuration, icon information, and version resources.

        Raises:
            InspectError: If inspection fails and the native layer reports an
                inspection-specific error.
            RuntimeError: If the `bat2pe._native` extension is unavailable.

        Examples:
            Inspect a generated executable:

                result = Inspector("dist/hello.exe").inspect()
        """

        native = _load_native()
        try:
            payload = native.inspect(_normalize_path(self.executable))
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, InspectError) from exc
        return InspectResult.from_dict(json.loads(payload))


class Verifier:
    """Stateful verifier for comparing script and executable behavior.

    The verifier runs the original batch script and the generated executable
    with matching inputs, then compares their observable results.

    Examples:
        Verify that an executable behaves like its source script:

            verifier = Verifier(
                "scripts/hello.bat",
                "dist/hello.exe",
                args=["world"],
            )
            result = verifier.verify()
    """

    def __init__(
        self,
        script: Pathish,
        executable: Pathish,
        *,
        args: Iterable[str] | None = None,
        cwd: Pathish | None = None,
    ) -> None:
        """Initialize a verification request.

        Args:
            script: Path to the original batch script used as the behavior
                baseline.
            executable: Path to the generated executable to compare against the
                original script.
            args: Optional command-line arguments passed to both the script and
                the executable during verification.
            cwd: Optional working directory used for both executions. When
                omitted, the native verifier uses its default working
                directory.
        """

        self.script = Path(script)
        self.executable = Path(executable)
        self.args = list(args or [])
        self.cwd = Path(cwd) if cwd is not None else None

    def verify(self) -> VerifyResult:
        """Execute verification with the options stored on this instance.

        Returns:
            VerifyResult: Execution outputs for the script and executable plus
            comparison flags that show whether they match.

        Raises:
            VerifyError: If verification cannot be completed before a
                comparison result is produced.
            RuntimeError: If the `bat2pe._native` extension is unavailable.

        Examples:
            Verify a generated executable with shared CLI arguments:

                result = Verifier(
                    "scripts/hello.bat",
                    "dist/hello.exe",
                    args=["--quiet"],
                ).verify()
        """

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
    output_exe: Pathish | None = None,
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
    """Build an executable with the functional convenience API.

    This is a thin wrapper around `Builder(...).build()` for callers that do
    not need to keep a builder instance around.

    Args:
        input_script: Path to the source batch script to embed.
        output_exe: Optional output path for the generated executable. When
            omitted, the native builder chooses the destination path.
        window: Window visibility mode forwarded to the native builder. Common
            values are `"visible"` and `"hidden"`.
        icon: Optional path to an `.ico` file embedded into the executable.
        company: Optional company name written into version metadata.
        product: Optional product name written into version metadata.
        description: Optional file description written into version metadata.
        file_version: Optional file version string written into version
            metadata.
        product_version: Optional product version string written into version
            metadata.
        original_filename: Optional original filename recorded in version
            metadata.
        internal_name: Optional internal name recorded in version metadata.
        stub_console: Optional path to the console-mode stub executable.
        stub_windows: Optional path to the windowed stub executable.

    Returns:
        BuildResult: Build metadata plus an inspection snapshot of the
        generated executable.

    Raises:
        BuildError: If the native build process fails and returns a mapped
            bat2pe build error.
        RuntimeError: If the `bat2pe._native` extension is unavailable.

    Examples:
        Build an executable in one call:

            result = build(
                input_script="scripts/hello.bat",
                output_exe="dist/hello.exe",
                company="Example Co.",
            )
    """

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
    """Inspect a generated executable with the functional convenience API.

    This is a thin wrapper around `Inspector(executable).inspect()`.

    Args:
        executable: Path to the generated executable that should be inspected.

    Returns:
        InspectResult: Parsed executable metadata, including runtime settings,
        icon information, and version resources.

    Raises:
        InspectError: If inspection fails and the native layer reports an
            inspection-specific error.
        RuntimeError: If the `bat2pe._native` extension is unavailable.

    Examples:
        Inspect an executable in one call:

            result = inspect("dist/hello.exe")
    """

    return Inspector(executable).inspect()


def verify(
    script: Pathish,
    executable: Pathish,
    *,
    args: Iterable[str] | None = None,
    cwd: Pathish | None = None,
) -> VerifyResult:
    """Verify a generated executable with the functional convenience API.

    This is a thin wrapper around `Verifier(...).verify()`.

    Args:
        script: Path to the original batch script used as the verification
            baseline.
        executable: Path to the generated executable that should behave like
            the original script.
        args: Optional command-line arguments passed to both the script and the
            executable during verification.
        cwd: Optional working directory used for both executions.

    Returns:
        VerifyResult: Execution outputs for the script and executable plus
        comparison flags.

    Raises:
        VerifyError: If verification cannot be completed before a comparison
            result is produced.
        RuntimeError: If the `bat2pe._native` extension is unavailable.

    Examples:
        Compare a script and executable in one call:

            result = verify(
                "scripts/hello.bat",
                "dist/hello.exe",
                args=["--quiet"],
            )
    """

    return Verifier(script, executable, args=args, cwd=cwd).verify()
