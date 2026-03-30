from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class VersionTriplet:
    major: int
    minor: int
    patch: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VersionTriplet":
        return cls(
            major=int(data["major"]),
            minor=int(data["minor"]),
            patch=int(data["patch"]),
        )


@dataclass(slots=True, frozen=True)
class VersionInfo:
    company_name: str | None = None
    product_name: str | None = None
    file_description: str | None = None
    file_version: VersionTriplet | None = None
    product_version: VersionTriplet | None = None
    original_filename: str | None = None
    internal_name: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VersionInfo":
        return cls(
            company_name=data.get("company_name"),
            product_name=data.get("product_name"),
            file_description=data.get("file_description"),
            file_version=(
                VersionTriplet.from_dict(data["file_version"])
                if data.get("file_version") is not None
                else None
            ),
            product_version=(
                VersionTriplet.from_dict(data["product_version"])
                if data.get("product_version") is not None
                else None
            ),
            original_filename=data.get("original_filename"),
            internal_name=data.get("internal_name"),
        )


@dataclass(slots=True, frozen=True)
class IconInfo:
    file_name: str
    source_path: Path
    size: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IconInfo":
        return cls(
            file_name=str(data["file_name"]),
            source_path=Path(data["source_path"]),
            size=int(data["size"]),
        )


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    window_mode: str
    temp_script_suffix: str
    strict_dp0: bool
    uac: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeConfig":
        return cls(
            window_mode=str(data["window_mode"]),
            temp_script_suffix=str(data["temp_script_suffix"]),
            strict_dp0=bool(data["strict_dp0"]),
            uac=bool(data.get("uac", False)),
        )


@dataclass(slots=True, frozen=True)
class InspectResult:
    exe_path: Path
    source_script_name: str
    source_extension: str
    script_encoding: str
    script_length: int
    runtime: RuntimeConfig
    icon: IconInfo | None
    version_info: VersionInfo
    schema_version: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InspectResult":
        return cls(
            exe_path=Path(data["exe_path"]),
            source_script_name=str(data["source_script_name"]),
            source_extension=str(data["source_extension"]),
            script_encoding=str(data["script_encoding"]),
            script_length=int(data["script_length"]),
            runtime=RuntimeConfig.from_dict(data["runtime"]),
            icon=IconInfo.from_dict(data["icon"]) if data.get("icon") is not None else None,
            version_info=VersionInfo.from_dict(data["version_info"]),
            schema_version=int(data["schema_version"]),
        )


@dataclass(slots=True, frozen=True)
class BuildResult:
    output_exe_path: Path
    template_executable_path: Path
    script_encoding: str
    script_length: int
    window_mode: str
    uac: bool
    inspect: InspectResult

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BuildResult":
        output_exe_value = data.get("output_exe_path")
        if output_exe_value is None:
            output_exe_value = data["output_exe"]
        template_executable_value = data.get("template_executable_path")
        if template_executable_value is None:
            template_executable_value = data["stub_path"]

        return cls(
            output_exe_path=Path(output_exe_value),
            template_executable_path=Path(template_executable_value),
            script_encoding=str(data["script_encoding"]),
            script_length=int(data["script_length"]),
            window_mode=str(data["window_mode"]),
            uac=bool(data.get("uac", False)),
            inspect=InspectResult.from_dict(data["inspect"]),
        )

    @property
    def output_exe(self) -> Path:
        return self.output_exe_path

    @property
    def stub_path(self) -> Path:
        return self.template_executable_path


@dataclass(slots=True, frozen=True)
class VerifyExecution:
    exit_code: int
    stderr: str
    stdout: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerifyExecution":
        return cls(
            exit_code=int(data["exit_code"]),
            stderr=str(data["stderr"]),
            stdout=str(data["stdout"]),
        )


@dataclass(slots=True, frozen=True)
class VerifyResult:
    script: VerifyExecution
    executable: VerifyExecution
    exit_code_match: bool
    stderr_match: bool
    success: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerifyResult":
        return cls(
            script=VerifyExecution.from_dict(data["script"]),
            executable=VerifyExecution.from_dict(data["executable"]),
            exit_code_match=bool(data["exit_code_match"]),
            stderr_match=bool(data["stderr_match"]),
            success=bool(data["success"]),
        )
