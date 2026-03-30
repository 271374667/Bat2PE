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


def _validate_existing_file_path(value: Pathish, *, arg_name: str) -> Path:
    """Validate that a required path-like argument points to an existing file."""

    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{arg_name!r} must not be empty")

    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"{arg_name!r} does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"{arg_name!r} must point to an existing file: {path}")
    return path


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


def _resolve_alias(
    preferred_value,
    legacy_value,
    *,
    preferred_name: str,
    legacy_name: str,
    required: bool = False,
):
    if preferred_value is not None and legacy_value is not None:
        raise TypeError(f"pass either {preferred_name!r} or {legacy_name!r}, not both")

    value = preferred_value if preferred_value is not None else legacy_value
    if required and value is None:
        raise TypeError(f"missing required argument: {preferred_name!r}")
    return value


class Builder:
    """Stateful builder for converting a batch script into an executable.

    This class is the object-oriented entry point for build configuration. It
    stores all build inputs on the instance so callers can prepare options once
    and invoke `build()` later.

    Examples:
        Build a hidden-window executable with version metadata:

            builder = Builder(
                input_bat_path="scripts/hello.bat",
                output_exe_path="dist/hello.exe",
                window="hidden",
                company="Example Co.",
                product="Batch Tools",
                file_version="1.2.0",
            )
            result = builder.build()
    """

    def __init__(
        self,
        input_bat_path: Pathish,
        *,
        output_exe_path: Pathish | None = None,
        window: str = "visible",
        uac: bool = False,
        icon_path: Pathish | None = None,
        company: str | None = None,
        product: str | None = None,
        description: str | None = None,
        file_version: str | None = None,
        product_version: str | None = None,
        original_filename: str | None = None,
        internal_name: str | None = None,
        stub_console_path: Pathish | None = None,
        stub_windows_path: Pathish | None = None,
        output_exe: Pathish | None = None,
        icon: Pathish | None = None,
        stub_console: Pathish | None = None,
        stub_windows: Pathish | None = None,
    ) -> None:
        """Initialize a build request.

        Args:
            input_bat_path: Path to the source batch script to embed into the
                generated executable.
            output_exe_path: Optional output path for the generated executable. When
                omitted, the native builder decides the final output location.
            window: Window visibility mode forwarded to the native builder.
                Common values are `"visible"` and `"hidden"`.
            icon_path: Optional path to an `.ico` file embedded into the executable.
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
            stub_console_path: Optional path to the console-mode stub executable.
                When omitted, bat2pe attempts to discover a packaged or locally
                built stub automatically.
            stub_windows_path: Optional path to the windowed stub executable. When
                omitted, bat2pe attempts to discover a packaged or locally
                built stub automatically.
        """

        resolved_output_exe_path = _resolve_alias(
            output_exe_path,
            output_exe,
            preferred_name="output_exe_path",
            legacy_name="output_exe",
        )
        resolved_icon_path = _resolve_alias(
            icon_path,
            icon,
            preferred_name="icon_path",
            legacy_name="icon",
        )
        resolved_stub_console_path = _resolve_alias(
            stub_console_path,
            stub_console,
            preferred_name="stub_console_path",
            legacy_name="stub_console",
        )
        resolved_stub_windows_path = _resolve_alias(
            stub_windows_path,
            stub_windows,
            preferred_name="stub_windows_path",
            legacy_name="stub_windows",
        )

        self.input_bat_path = _validate_existing_file_path(
            input_bat_path,
            arg_name="input_bat_path",
        )
        self.output_exe_path = (
            Path(resolved_output_exe_path) if resolved_output_exe_path is not None else None
        )
        self.window = window
        self.uac = uac
        self.icon_path = Path(resolved_icon_path) if resolved_icon_path is not None else None
        self.company = company
        self.product = product
        self.description = description
        self.file_version = file_version
        self.product_version = product_version
        self.original_filename = original_filename
        self.internal_name = internal_name
        self.stub_console_path = (
            Path(resolved_stub_console_path) if resolved_stub_console_path is not None else None
        )
        self.stub_windows_path = (
            Path(resolved_stub_windows_path) if resolved_stub_windows_path is not None else None
        )

    @property
    def input_script(self) -> Path:
        return self.input_bat_path

    @property
    def output_exe(self) -> Path | None:
        return self.output_exe_path

    @property
    def icon(self) -> Path | None:
        return self.icon_path

    @property
    def stub_console(self) -> Path | None:
        return self.stub_console_path

    @property
    def stub_windows(self) -> Path | None:
        return self.stub_windows_path

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

                result = Builder(input_bat_path="hello.bat").build()
        """

        native = _load_native()
        try:
            payload = native.build(
                _normalize_path(self.input_bat_path),
                _normalize_path(self.output_exe_path)
                if self.output_exe_path is not None
                else None,
                window=self.window,
                uac=self.uac,
                icon_path=_normalize_path(self.icon_path) if self.icon_path is not None else None,
                company=self.company,
                product=self.product,
                description=self.description,
                file_version=self.file_version,
                product_version=self.product_version,
                original_filename=self.original_filename,
                internal_name=self.internal_name,
                stub_console_path=_normalize_path(self.stub_console_path)
                if self.stub_console_path is not None
                else _find_stub("bat2pe-stub-console"),
                stub_windows_path=_normalize_path(self.stub_windows_path)
                if self.stub_windows_path is not None
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

    def __init__(
        self,
        executable_path: Pathish | None = None,
        executable: Pathish | None = None,
    ) -> None:
        """Initialize an inspector for a generated executable.

        Args:
            executable_path: Path to the `.exe` file produced by bat2pe and targeted
                for inspection.
        """

        resolved_executable_path = _resolve_alias(
            executable_path,
            executable,
            preferred_name="executable_path",
            legacy_name="executable",
            required=True,
        )
        self.executable_path = Path(resolved_executable_path)

    @property
    def executable(self) -> Path:
        return self.executable_path

    def inspect(self) -> InspectResult:
        """Inspect the configured executable and decode its embedded metadata.

        Returns:
            InspectResult: Parsed executable metadata, including runtime
            configuration, icon information, and version metadata.

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
            payload = native.inspect(_normalize_path(self.executable_path))
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
        script_path: Pathish | None = None,
        executable_path: Pathish | None = None,
        *,
        args: Iterable[str] | None = None,
        cwd_path: Pathish | None = None,
        script: Pathish | None = None,
        executable: Pathish | None = None,
        cwd: Pathish | None = None,
    ) -> None:
        """Initialize a verification request.

        Args:
            script_path: Path to the original batch script used as the behavior
                baseline.
            executable_path: Path to the generated executable to compare against the
                original script.
            args: Optional command-line arguments passed to both the script and
                the executable during verification.
            cwd_path: Optional working directory used for both executions. When
                omitted, the native verifier uses its default working
                directory.
        """

        resolved_script_path = _resolve_alias(
            script_path,
            script,
            preferred_name="script_path",
            legacy_name="script",
            required=True,
        )
        resolved_executable_path = _resolve_alias(
            executable_path,
            executable,
            preferred_name="executable_path",
            legacy_name="executable",
            required=True,
        )
        resolved_cwd_path = _resolve_alias(
            cwd_path,
            cwd,
            preferred_name="cwd_path",
            legacy_name="cwd",
        )

        self.script_path = Path(resolved_script_path)
        self.executable_path = Path(resolved_executable_path)
        self.args = list(args or [])
        self.cwd_path = Path(resolved_cwd_path) if resolved_cwd_path is not None else None

    @property
    def script(self) -> Path:
        return self.script_path

    @property
    def executable(self) -> Path:
        return self.executable_path

    @property
    def cwd(self) -> Path | None:
        return self.cwd_path

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
                _normalize_path(self.script_path),
                _normalize_path(self.executable_path),
                args=self.args,
                cwd=_normalize_path(self.cwd_path) if self.cwd_path is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, VerifyError) from exc
        return VerifyResult.from_dict(json.loads(payload))


def build(
    input_bat_path: Pathish,
    *,
    output_exe_path: Pathish | None = None,
    window: str = "visible",
    uac: bool = False,
    icon_path: Pathish | None = None,
    company: str | None = None,
    product: str | None = None,
    description: str | None = None,
    file_version: str | None = None,
    product_version: str | None = None,
    original_filename: str | None = None,
    internal_name: str | None = None,
    stub_console_path: Pathish | None = None,
    stub_windows_path: Pathish | None = None,
    output_exe: Pathish | None = None,
    icon: Pathish | None = None,
    stub_console: Pathish | None = None,
    stub_windows: Pathish | None = None,
) -> BuildResult:
    """Build an executable with the functional convenience API.

    This is a thin wrapper around `Builder(...).build()` for callers that do
    not need to keep a builder instance around.

    Args:
        input_bat_path: Path to the source batch script to embed.
        output_exe_path: Optional output path for the generated executable. When
            omitted, the native builder chooses the destination path.
        window: Window visibility mode forwarded to the native builder. Common
            values are `"visible"` and `"hidden"`.
        icon_path: Optional path to an `.ico` file embedded into the executable.
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
        stub_console_path: Optional path to the console-mode stub executable.
        stub_windows_path: Optional path to the windowed stub executable.

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
                input_bat_path="scripts/hello.bat",
                output_exe_path="dist/hello.exe",
                company="Example Co.",
            )
    """

    return Builder(
        input_bat_path=input_bat_path,
        output_exe_path=output_exe_path,
        window=window,
        uac=uac,
        icon_path=icon_path,
        company=company,
        product=product,
        description=description,
        file_version=file_version,
        product_version=product_version,
        original_filename=original_filename,
        internal_name=internal_name,
        stub_console_path=stub_console_path,
        stub_windows_path=stub_windows_path,
        output_exe=output_exe,
        icon=icon,
        stub_console=stub_console,
        stub_windows=stub_windows,
    ).build()


def inspect(
    executable_path: Pathish | None = None,
    executable: Pathish | None = None,
) -> InspectResult:
    """Inspect a generated executable with the functional convenience API.

    This is a thin wrapper around `Inspector(executable_path).inspect()`.

    Args:
        executable_path: Path to the generated executable that should be inspected.

    Returns:
        InspectResult: Parsed executable metadata, including runtime settings,
        icon information, and version metadata.

    Raises:
        InspectError: If inspection fails and the native layer reports an
            inspection-specific error.
        RuntimeError: If the `bat2pe._native` extension is unavailable.

    Examples:
        Inspect an executable in one call:

            result = inspect("dist/hello.exe")
    """

    return Inspector(executable_path=executable_path, executable=executable).inspect()


def verify(
    script_path: Pathish | None = None,
    executable_path: Pathish | None = None,
    *,
    args: Iterable[str] | None = None,
    cwd_path: Pathish | None = None,
    script: Pathish | None = None,
    executable: Pathish | None = None,
    cwd: Pathish | None = None,
) -> VerifyResult:
    """Verify a generated executable with the functional convenience API.

    This is a thin wrapper around `Verifier(...).verify()`.

    Args:
        script_path: Path to the original batch script used as the verification
            baseline.
        executable_path: Path to the generated executable that should behave like
            the original script.
        args: Optional command-line arguments passed to both the script and the
            executable during verification.
        cwd_path: Optional working directory used for both executions.

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

    return Verifier(
        script_path=script_path,
        executable_path=executable_path,
        args=args,
        cwd_path=cwd_path,
        script=script,
        executable=executable,
        cwd=cwd,
    ).verify()
