"""Object-oriented and functional Python API for bat2pe.

`Builder` is the primary object-oriented entry point. The top-level `build()`
helper is a thin wrapper for callers that prefer a functional style.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import TypeAlias

from ._errors import BuildError, map_native_error
from ._models import BuildResult

Pathish: TypeAlias = str | Path

logger = logging.getLogger(__name__)


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


class Builder:
    """Stateful builder for converting a batch script into an executable.

    This class is the object-oriented entry point for build configuration. It
    stores all build inputs on the instance so callers can prepare options once
    and invoke `build()` later.

    Examples:
        Build an executable with version metadata (hidden window by default):

            builder = Builder(
                input_bat_path="scripts/hello.bat",
                output_exe_path="dist/hello.exe",
                company_name="Example Co.",
                product_name="Batch Tools",
                file_version="1.2.0",
            )
            result = builder.build()
    """

    def __init__(
        self,
        input_bat_path: Pathish,
        *,
        output_exe_path: Pathish | None = None,
        visible: bool = False,
        uac: bool = False,
        icon_path: Pathish | None = None,
        company_name: str | None = None,
        product_name: str | None = None,
        description: str | None = None,
        file_version: str | None = None,
        product_version: str | None = None,
        original_filename: str | None = None,
        internal_name: str | None = None,
    ) -> None:
        """Initialize a build request.

        Args:
            input_bat_path: Path to the source batch script to embed into the
                generated executable.
            output_exe_path: Optional output path for the generated executable. When
                omitted, the native builder decides the final output location.
            visible: Whether the generated executable should show a console
                window when launched. Defaults to ``False`` (hidden window).
            icon_path: Optional path to an `.ico` file embedded into the executable.
            company_name: Optional company name written into version metadata.
            product_name: Optional product name written into version metadata.
            description: Optional file description written into version
                metadata.
            file_version: Optional file version string written into version
                metadata.
            product_version: Optional product version string written into
                version metadata.
            original_filename: Optional original filename recorded in version
                metadata. Defaults to the generated output file name when omitted.
            internal_name: Optional internal name recorded in version
                metadata. Defaults to the generated output file stem when omitted.
        """

        self.input_bat_path = Path(input_bat_path)
        self.output_exe_path = (
            Path(output_exe_path) if output_exe_path is not None else None
        )
        self.visible = visible
        self.uac = uac
        self.icon_path = Path(icon_path) if icon_path is not None else None
        self.company_name = company_name
        self.product_name = product_name
        self.description = description
        self.file_version = file_version
        self.product_version = product_version
        self.original_filename = original_filename
        self.internal_name = internal_name

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

        target_output_path = self.output_exe_path or self.input_bat_path.with_suffix(".exe")
        target_output_path.parent.mkdir(parents=True, exist_ok=True)
        if target_output_path.exists():
            logger.info("Overwriting existing file: %s", target_output_path)

        native = _load_native()
        try:
            payload = native.build(
                _normalize_path(self.input_bat_path),
                _normalize_path(self.output_exe_path)
                if self.output_exe_path is not None
                else None,
                visible=self.visible,
                uac=self.uac,
                icon_path=_normalize_path(self.icon_path) if self.icon_path is not None else None,
                company_name=self.company_name,
                product_name=self.product_name,
                description=self.description,
                file_version=self.file_version,
                product_version=self.product_version,
                original_filename=self.original_filename,
                internal_name=self.internal_name,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_native_error(exc, BuildError) from exc
        return BuildResult.from_dict(json.loads(payload))


def build(
    input_bat_path: Pathish,
    *,
    output_exe_path: Pathish | None = None,
    visible: bool = False,
    uac: bool = False,
    icon_path: Pathish | None = None,
    company_name: str | None = None,
    product_name: str | None = None,
    description: str | None = None,
    file_version: str | None = None,
    product_version: str | None = None,
    original_filename: str | None = None,
    internal_name: str | None = None,
) -> BuildResult:
    """Build an executable with the functional convenience API.

    This is a thin wrapper around `Builder(...).build()` for callers that do
    not need to keep a builder instance around.

    Args:
        input_bat_path: Path to the source batch script to embed.
        output_exe_path: Optional output path for the generated executable. When
            omitted, the native builder chooses the destination path.
        visible: Whether the generated executable should show a console window
            when launched. Defaults to ``False`` (hidden window).
        icon_path: Optional path to an `.ico` file embedded into the executable.
        company_name: Optional company name written into version metadata.
        product_name: Optional product name written into version metadata.
        description: Optional file description written into version metadata.
        file_version: Optional file version string written into version
            metadata.
        product_version: Optional product version string written into version
            metadata.
        original_filename: Optional original filename recorded in version
            metadata. Defaults to the generated output file name when omitted.
        internal_name: Optional internal name recorded in version
            metadata. Defaults to the generated output file stem when omitted.

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
                company_name="Example Co.",
            )
    """

    return Builder(
        input_bat_path=input_bat_path,
        output_exe_path=output_exe_path,
        visible=visible,
        uac=uac,
        icon_path=icon_path,
        company_name=company_name,
        product_name=product_name,
        description=description,
        file_version=file_version,
        product_version=product_version,
        original_filename=original_filename,
        internal_name=internal_name,
    ).build()
