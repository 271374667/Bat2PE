from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from bat2pe import BuildError
from bat2pe._errors import map_native_error
from bat2pe._models import BuildResult


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


def test_build_result_from_dict() -> None:
    payload = {
        "output_exe_path": "dist/demo.exe",
        "stub_path": "target/debug/bat2pe-stub-console.exe",
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
) -> None:
    assert bat2pe_module.Builder.__name__ == "Builder"
    assert bat2pe_module.build.__name__ == "build"
    assert bat2pe_module.inspect.__name__ == "inspect"
    assert bat2pe_module.verify.__name__ == "verify"
    assert "Builder" in bat2pe_module.__all__
    assert "build" in bat2pe_module.__all__
    assert "verify" in bat2pe_module.__all__

    script = test_dir / "functional_api.bat"
    script.write_bytes(b"@echo off\r\necho functional api 1>&2\r\nexit /b 4\r\n")
    output = test_dir / "functional_api.exe"
    icon = test_dir / "functional_api.ico"
    icon.write_bytes(fake_ico_bytes)

    output.write_text("stale exe placeholder", encoding="utf-8")
    build_result = bat2pe_module.build(
        input_bat_path=script,
        icon_path=icon,
        company="Acme",
        product="Functional API",
    )

    assert build_result.output_exe_path == output
    assert build_result.uac is False
    inspect_result = bat2pe_module.inspect(output)
    assert inspect_result.source_script_name == "functional_api.bat"
    assert inspect_result.runtime.uac is False
    verify_result = bat2pe_module.verify(script, output)
    assert verify_result.success is True
    assert _extract_icon_count(output) > 0
    assert _extract_execution_level(output) == "asInvoker"


def test_python_builder_inspector_verifier_roundtrip(
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
    cwd = test_dir / "python cwd"
    cwd.mkdir()

    builder = bat2pe_module.Builder(
        input_bat_path=script,
        output_exe_path=output,
        window="visible",
        icon_path=icon,
        company="Acme",
        product="Runner",
        description="Python API test",
        file_version="2.3.4",
        product_version="5.6.7",
        original_filename="python_api.exe",
        internal_name="python_api",
    )
    build_result = builder.build()

    assert build_result.output_exe_path == output
    assert build_result.stub_path.name == "bat2pe-stub-console.exe"
    assert build_result.script_encoding == "utf8"
    assert build_result.uac is False
    assert build_result.inspect.source_extension == ".cmd"
    assert build_result.inspect.version_info.company_name == "Acme"
    assert build_result.inspect.runtime.uac is False
    assert build_result.inspect.icon is not None
    assert build_result.inspect.icon.source_path == icon

    inspector = bat2pe_module.Inspector(output)
    inspect_result = inspector.inspect()
    assert inspect_result.exe_path == output
    assert inspect_result.source_script_name == "python_api.cmd"
    assert inspect_result.version_info.product_version.patch == 7

    verifier = bat2pe_module.Verifier(
        script,
        output,
        args=["alpha beta", "gamma"],
        cwd=cwd,
    )
    verify_result = verifier.verify()
    assert verify_result.success is True
    assert verify_result.script.exit_code == 9
    assert f"cwd={cwd}" in verify_result.script.stderr
    assert "arg1=alpha beta" in verify_result.script.stderr
    assert "arg2=gamma" in verify_result.script.stderr
    assert _extract_icon_count(output) > 0
    assert _extract_execution_level(output) == "asInvoker"


def test_python_api_builds_uac_enabled_executable_and_rejects_verify(
    bat2pe_module,
    test_dir: Path,
) -> None:
    script = test_dir / "python_uac.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "python_uac.exe"

    build_result = bat2pe_module.build(
        input_bat_path=script,
        output_exe_path=output,
        uac=True,
    )

    assert build_result.uac is True
    assert build_result.inspect.runtime.uac is True
    assert _extract_execution_level(output) == "requireAdministrator"

    inspect_result = bat2pe_module.inspect(output)
    assert inspect_result.runtime.uac is True

    with pytest.raises(bat2pe_module.VerifyError) as verify_error:
        bat2pe_module.verify(script, output)

    assert verify_error.value.code == 109
    assert "does not support uac-enabled executables" in str(verify_error.value)


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

    invalid_exe = test_dir / "invalid.exe"
    invalid_exe.write_text("plain text", encoding="utf-8")
    with pytest.raises(bat2pe_module.InspectError) as inspect_error:
        bat2pe_module.Inspector(invalid_exe).inspect()

    assert inspect_error.value.code == 104
    assert inspect_error.value.path is None

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


def test_python_builder_rejects_empty_input_bat_path(bat2pe_module) -> None:
    with pytest.raises(ValueError, match="input_bat_path"):
        bat2pe_module.Builder(input_bat_path="")


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

    with pytest.raises(FileNotFoundError, match="input_bat_path"):
        bat2pe_module.Builder(input_bat_path=missing_script)


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


def test_python_api_reports_missing_stub_paths(
    bat2pe_module,
    monkeypatch: pytest.MonkeyPatch,
    test_dir: Path,
) -> None:
    script = test_dir / "missing_stubs.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")

    monkeypatch.delenv("BAT2PE_STUB_CONSOLE", raising=False)
    monkeypatch.delenv("BAT2PE_STUB_WINDOWS", raising=False)
    monkeypatch.setattr("bat2pe._api._find_stub", lambda _name: None)

    with pytest.raises(bat2pe_module.BuildError) as build_error:
        bat2pe_module.build(
            input_bat_path=script,
            output_exe_path=test_dir / "missing_stubs.exe",
        )

    assert build_error.value.code == 103
