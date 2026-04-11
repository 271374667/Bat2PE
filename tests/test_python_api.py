from __future__ import annotations

import ctypes
import inspect as pyinspect
import logging
from pathlib import Path

import pytest

from bat2pe import BuildError
from bat2pe._errors import map_native_error
from bat2pe._models import BuildResult


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def _extract_icon_count(executable_path: Path) -> int:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ExtractIconExW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    shell32.ExtractIconExW.restype = ctypes.c_uint
    return int(shell32.ExtractIconExW(str(executable_path), -1, None, None, 0))


def _read_manifest_resource(executable_path: Path) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.FindResourceW.restype = ctypes.c_void_p
    kernel32.SizeofResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.SizeofResource.restype = ctypes.c_uint32
    kernel32.LoadResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.LoadResource.restype = ctypes.c_void_p
    kernel32.LockResource.argtypes = [ctypes.c_void_p]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    kernel32.FreeLibrary.restype = ctypes.c_int

    module = kernel32.LoadLibraryExW(str(executable_path), None, 0x00000002)
    if not module:
        raise OSError(ctypes.get_last_error(), f"failed to load {executable_path}")

    try:
        manifest_resource = kernel32.FindResourceW(module, ctypes.c_void_p(1), ctypes.c_void_p(24))
        if not manifest_resource:
            raise OSError(ctypes.get_last_error(), f"missing manifest resource in {executable_path}")

        size = kernel32.SizeofResource(module, manifest_resource)
        loaded = kernel32.LoadResource(module, manifest_resource)
        locked = kernel32.LockResource(loaded)
        if not loaded or not locked or size == 0:
            raise OSError(
                ctypes.get_last_error(),
                f"failed to read manifest resource from {executable_path}",
            )

        return ctypes.string_at(locked, size).decode("utf-8")
    finally:
        kernel32.FreeLibrary(module)


def _extract_execution_level(executable_path: Path) -> str:
    manifest = _read_manifest_resource(executable_path)
    if "requireAdministrator" in manifest:
        return "requireAdministrator"
    if "asInvoker" in manifest:
        return "asInvoker"
    raise AssertionError(f"unexpected manifest payload: {manifest}")


def _load_version_blob(executable_path: Path) -> ctypes.Array[ctypes.c_ubyte]:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version.GetFileVersionInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    version.GetFileVersionInfoW.restype = ctypes.c_int

    size = version.GetFileVersionInfoSizeW(str(executable_path), None)
    if size == 0:
        raise OSError(ctypes.get_last_error(), f"missing version resource in {executable_path}")

    buffer = (ctypes.c_ubyte * size)()
    ok = version.GetFileVersionInfoW(str(executable_path), 0, size, ctypes.byref(buffer))
    if not ok:
        raise OSError(ctypes.get_last_error(), f"failed to load version resource from {executable_path}")

    return buffer


def _query_version_value(buffer: ctypes.Array[ctypes.c_ubyte], sub_block: str) -> tuple[ctypes.c_void_p, int]:
    version = ctypes.WinDLL("version", use_last_error=True)
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    version.VerQueryValueW.restype = ctypes.c_int

    value_ptr = ctypes.c_void_p()
    value_len = ctypes.c_uint32()
    ok = version.VerQueryValueW(
        ctypes.byref(buffer),
        sub_block,
        ctypes.byref(value_ptr),
        ctypes.byref(value_len),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), f"missing version sub-block {sub_block!r}")

    return value_ptr, int(value_len.value)


def _read_version_string(executable_path: Path, key: str) -> str:
    buffer = _load_version_blob(executable_path)
    value_ptr, _ = _query_version_value(buffer, rf"\StringFileInfo\040904B0\{key}")
    return ctypes.wstring_at(value_ptr)


def _read_fixed_version(executable_path: Path, kind: str) -> tuple[int, int, int]:
    buffer = _load_version_blob(executable_path)
    value_ptr, value_len = _query_version_value(buffer, "\\")
    assert value_len >= ctypes.sizeof(_VSFixedFileInfo)
    info = ctypes.cast(value_ptr, ctypes.POINTER(_VSFixedFileInfo)).contents

    if kind == "file":
        major_minor = info.dwFileVersionMS
        patch_build = info.dwFileVersionLS
    else:
        major_minor = info.dwProductVersionMS
        patch_build = info.dwProductVersionLS

    return (
        (major_minor >> 16) & 0xFFFF,
        major_minor & 0xFFFF,
        (patch_build >> 16) & 0xFFFF,
    )


def test_build_result_from_dict() -> None:
    payload = {
        "output_exe_path": "dist/demo.exe",
        "template_executable_path": "embedded-bat2pe-runtime-host.exe",
        "script_encoding": "utf8",
        "script_length": 12,
        "window_mode": "visible",
        "uac": False,
        "inspect": {
            "exe_path": "dist/demo.exe",
            "source_script_name": "demo.bat",
            "source_extension": ".bat",
            "script_encoding": "utf8",
            "script_length": 12,
            "runtime": {
                "window_mode": "visible",
                "temp_script_suffix": ".cmd",
                "strict_dp0": True,
                "uac": False,
            },
            "icon": None,
            "version_info": {},
            "schema_version": 1,
        },
    }

    result = BuildResult.from_dict(payload)
    assert result.output_exe_path == Path("dist/demo.exe")
    assert result.uac is False
    assert result.inspect.runtime.temp_script_suffix == ".cmd"


def test_version_triplet_str() -> None:
    from bat2pe._models import VersionTriplet

    triplet = VersionTriplet(major=1, minor=2, patch=3)
    assert str(triplet) == "1.2.3"
    assert f"v{triplet}" == "v1.2.3"


def test_error_code_constants_match_rust() -> None:
    from bat2pe import (
        ERR_CLI_USAGE,
        ERR_DIRECTORY_NOT_WRITABLE,
        ERR_INVALID_EXECUTABLE,
        ERR_INVALID_INPUT,
        ERR_IO,
        ERR_RESOURCE_NOT_FOUND,
        ERR_UNSUPPORTED_ENCODING,
        ERR_UNSUPPORTED_INPUT,
        ERR_VERIFY_MISMATCH,
        ERR_VERIFY_UAC_INTERACTIVE,
    )

    assert ERR_INVALID_INPUT == 100
    assert ERR_UNSUPPORTED_INPUT == 101
    assert ERR_UNSUPPORTED_ENCODING == 102
    assert ERR_RESOURCE_NOT_FOUND == 103
    assert ERR_INVALID_EXECUTABLE == 104
    assert ERR_DIRECTORY_NOT_WRITABLE == 105
    assert ERR_IO == 106
    assert ERR_CLI_USAGE == 107
    assert ERR_VERIFY_MISMATCH == 108
    assert ERR_VERIFY_UAC_INTERACTIVE == 109


def test_native_error_mapping() -> None:
    error = map_native_error(
        RuntimeError('{"code":105,"message":"not writable","path":"C:/demo","details":"no access"}'),
        BuildError,
    )

    assert isinstance(error, BuildError)
    assert error.code == 105
    assert error.path == Path("C:/demo")
    assert error.details == "no access"


def test_native_error_mapping_falls_back_for_non_json() -> None:
    error = map_native_error(RuntimeError("plain failure"), BuildError)

    assert isinstance(error, BuildError)
    assert error.code == 1
    assert str(error) == "plain failure"


def test_top_level_import_surface_and_functional_api(
    bat2pe_module,
    fake_ico_bytes: bytes,
    test_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert bat2pe_module.Builder.__name__ == "Builder"
    assert bat2pe_module.build.__name__ == "build"
    assert "Builder" in bat2pe_module.__all__
    assert "build" in bat2pe_module.__all__
    assert "inspect" not in bat2pe_module.__all__
    assert "verify" not in bat2pe_module.__all__
    assert not hasattr(bat2pe_module, "Inspector")
    assert not hasattr(bat2pe_module, "Verifier")

    script = test_dir / "functional_api.bat"
    script.write_bytes(b"@echo off\r\necho functional api 1>&2\r\nexit /b 4\r\n")
    output = test_dir / "functional_api.exe"
    icon = test_dir / "functional_api.ico"
    icon.write_bytes(fake_ico_bytes)

    output.write_text("stale exe placeholder", encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="bat2pe._api"):
        build_result = bat2pe_module.build(
            input_bat_path=script,
            icon_path=icon,
            company_name="Acme",
            product_name="Functional API",
        )

    assert build_result.output_exe_path == output
    assert build_result.uac is False
    assert "Overwriting existing file" in caplog.text
    assert build_result.inspect.source_script_name == "functional_api.bat"
    assert build_result.inspect.runtime.uac is False
    assert _extract_icon_count(output) > 0
    assert _extract_execution_level(output) == "asInvoker"


def test_python_build_api_uses_canonical_output_and_icon_names(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "canonical_names.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")

    build_signature = pyinspect.signature(bat2pe_module.build)
    builder_signature = pyinspect.signature(bat2pe_module.Builder)

    assert "output_exe_path" in build_signature.parameters
    assert "icon_path" in build_signature.parameters
    assert "output_exe" not in build_signature.parameters
    assert "icon" not in build_signature.parameters
    assert "output_exe_path" in builder_signature.parameters
    assert "icon_path" in builder_signature.parameters
    assert "output_exe" not in builder_signature.parameters
    assert "icon" not in builder_signature.parameters
    assert "input_script" not in builder_signature.parameters

    builder = bat2pe_module.Builder(input_bat_path=script)

    assert not hasattr(builder, "output_exe")
    assert not hasattr(builder, "icon")
    assert not hasattr(builder, "input_script")


def test_python_build_api_rejects_removed_output_and_icon_aliases(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "removed_aliases.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "removed_aliases.exe"
    icon = test_dir / "removed_aliases.ico"
    icon.write_bytes(b"placeholder")

    with pytest.raises(TypeError, match="output_exe"):
        bat2pe_module.build(
            input_bat_path=script,
            output_exe=output,
        )

    with pytest.raises(TypeError, match="icon"):
        bat2pe_module.build(
            input_bat_path=script,
            icon=icon,
        )

    with pytest.raises(TypeError, match="output_exe"):
        bat2pe_module.Builder(
            input_bat_path=script,
            output_exe=output,
        )

    with pytest.raises(TypeError, match="icon"):
        bat2pe_module.Builder(
            input_bat_path=script,
            icon=icon,
        )


def test_python_builder_roundtrip(
    bat2pe_module,
    fake_ico_bytes: bytes,
    test_dir: Path,
) -> None:
    script = test_dir / "python_api.cmd"
    script.write_bytes(
        b"@echo off\r\n"
        b"echo cwd=%CD% 1>&2\r\n"
        b"echo arg1=%~1 1>&2\r\n"
        b"echo arg2=%~2 1>&2\r\n"
        b"exit /b 9\r\n"
    )
    output = test_dir / "python_api.exe"
    icon = test_dir / "python_api.ico"
    icon.write_bytes(fake_ico_bytes)

    builder = bat2pe_module.Builder(
        input_bat_path=script,
        output_exe_path=output,
        visible=True,
        icon_path=icon,
        company_name="Acme",
        product_name="Runner",
        description="Python API test",
        file_version="2.3.4",
        product_version="5.6.7",
        original_filename="python_api.exe",
        internal_name="python_api",
    )
    build_result = builder.build()

    assert build_result.output_exe_path == output
    assert build_result.template_executable_path.name == "embedded-bat2pe-runtime-host.exe"
    assert build_result.script_encoding == "utf8"
    assert build_result.uac is False
    assert build_result.inspect.source_extension == ".cmd"
    assert build_result.inspect.version_info.company_name == "Acme"
    assert build_result.inspect.runtime.uac is False
    assert build_result.inspect.icon is not None
    assert build_result.inspect.icon.source_path == icon
    assert _read_version_string(output, "CompanyName") == "Acme"
    assert _read_version_string(output, "ProductName") == "Runner"
    assert _read_version_string(output, "FileDescription") == "Python API test"
    assert _read_version_string(output, "OriginalFilename") == "python_api.exe"
    assert _read_version_string(output, "InternalName") == "python_api"
    assert _read_version_string(output, "FileVersion") == "2.3.4"
    assert _read_version_string(output, "ProductVersion") == "5.6.7"
    assert _read_fixed_version(output, "file") == (2, 3, 4)
    assert _read_fixed_version(output, "product") == (5, 6, 7)
    assert _extract_icon_count(output) > 0
    assert _extract_execution_level(output) == "asInvoker"


def test_python_api_builds_uac_enabled_executable(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "python_uac.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "python_uac.exe"

    build_result = bat2pe_module.build(
        input_bat_path=script,
        output_exe_path=output,
        visible=True,
        uac=True,
    )

    assert build_result.uac is True
    assert build_result.inspect.runtime.uac is True
    assert _extract_execution_level(output) == "requireAdministrator"


def test_python_api_raises_typed_errors(
    bat2pe_module,
    test_dir: Path,
) -> None:
    bad_script = test_dir / "bad.txt"
    bad_script.write_text("not a script", encoding="utf-8")
    output = test_dir / "bad.exe"

    with pytest.raises(bat2pe_module.BuildError) as build_error:
        bat2pe_module.Builder(
            input_bat_path=bad_script,
            output_exe_path=output,
        ).build()

    assert build_error.value.code == 101
    assert build_error.value.path == bad_script

    unsupported = test_dir / "unsupported.bat"
    unsupported.write_bytes(b"@\x00e\x00c\x00h\x00o\x00")
    with pytest.raises(bat2pe_module.BuildError) as unsupported_error:
        bat2pe_module.Builder(
            input_bat_path=unsupported,
            output_exe_path=test_dir / "unsupported.exe",
        ).build()

    assert unsupported_error.value.code == 102
    assert unsupported_error.value.path is None

    empty_script = test_dir / "empty.bat"
    empty_script.write_bytes(b"")
    with pytest.raises(bat2pe_module.BuildError) as empty_error:
        bat2pe_module.Builder(
            input_bat_path=empty_script,
            output_exe_path=test_dir / "empty.exe",
        ).build()

    assert empty_error.value.code == 100
    assert empty_error.value.path == empty_script

    real_script = test_dir / "real.bat"
    real_script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    with pytest.raises(bat2pe_module.BuildError) as version_error:
        bat2pe_module.build(
            input_bat_path=real_script,
            output_exe_path=test_dir / "bad_version.exe",
            file_version="1.2.3.4",
        )

    assert version_error.value.code == 100


def test_python_builder_requires_input_bat_path_argument(bat2pe_module) -> None:
    with pytest.raises(TypeError, match="input_bat_path"):
        bat2pe_module.Builder()


def test_python_build_requires_input_bat_path_argument(bat2pe_module) -> None:
    with pytest.raises(TypeError, match="input_bat_path"):
        bat2pe_module.build()


def test_python_builder_rejects_none_input_bat_path(bat2pe_module) -> None:
    with pytest.raises(TypeError):
        bat2pe_module.Builder(input_bat_path=None)


def test_python_builder_rejects_missing_input_bat_path_file(
    bat2pe_module,
    test_dir: Path,
) -> None:
    missing_script = test_dir / "missing.bat"

    with pytest.raises(bat2pe_module.BuildError) as missing_error:
        bat2pe_module.Builder(input_bat_path=missing_script).build()

    assert missing_error.value.path == missing_script


def test_python_builder_rejects_missing_icon_path(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "icon_check.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    missing_icon = test_dir / "missing.ico"

    with pytest.raises(bat2pe_module.BuildError) as missing_error:
        bat2pe_module.Builder(
            input_bat_path=script,
            icon_path=missing_icon,
        ).build()

    assert missing_error.value.path == missing_icon


def test_python_builder_defaults_output_path(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "builder_default_output.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    default_output = test_dir / "builder_default_output.exe"

    result = bat2pe_module.Builder(input_bat_path=script).build()

    assert result.output_exe_path == default_output
    assert default_output.exists()
    assert result.inspect.icon is not None
    assert result.inspect.icon.file_name == "MaterialSymbolsSdkOutlineRounded.ico"
    assert (
        result.inspect.icon.source_path.as_posix()
        == "embedded/MaterialSymbolsSdkOutlineRounded.ico"
    )
    assert result.inspect.version_info.original_filename == "builder_default_output.exe"
    assert result.inspect.version_info.internal_name == "builder_default_output"
    assert _read_version_string(default_output, "OriginalFilename") == "builder_default_output.exe"
    assert _read_version_string(default_output, "InternalName") == "builder_default_output"
    assert _extract_icon_count(default_output) > 0


def test_python_builder_uses_embedded_default_icon_when_icon_path_is_omitted(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "python_default_icon.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "python_default_icon.exe"

    result = bat2pe_module.build(
        input_bat_path=script,
        output_exe_path=output,
    )

    assert result.inspect.icon is not None
    assert result.inspect.icon.file_name == "MaterialSymbolsSdkOutlineRounded.ico"
    assert (
        result.inspect.icon.source_path.as_posix()
        == "embedded/MaterialSymbolsSdkOutlineRounded.ico"
    )
    assert result.inspect.icon.size > 0
    assert _extract_icon_count(output) > 0


def test_python_builder_creates_missing_output_parent_directory(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "nested_output.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "dist" / "nested" / "nested_output.exe"

    result = bat2pe_module.build(
        input_bat_path=script,
        output_exe_path=output,
    )

    assert result.output_exe_path == output
    assert output.parent.exists()
    assert output.exists()


def test_python_api_builds_without_packaged_template_executable(
    bat2pe_module,
    test_dir: Path,
) -> None:
    package_dir = Path(bat2pe_module.__file__).resolve().parent
    assert not (package_dir / "bin" / "bat2pe.exe").exists()

    script = test_dir / "embedded_template.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "embedded_template.exe"

    result = bat2pe_module.build(
        input_bat_path=script,
        output_exe_path=output,
    )

    assert result.output_exe_path == output
    assert output.exists()
