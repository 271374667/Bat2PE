from __future__ import annotations

from pathlib import Path

import pytest

from bat2pe import BuildError
from bat2pe._errors import map_native_error
from bat2pe._models import BuildResult


def test_build_result_from_dict() -> None:
    payload = {
        "output_exe": "dist/demo.exe",
        "stub_path": "target/debug/bat2pe-stub-console.exe",
        "script_encoding": "utf8",
        "script_length": 12,
        "window_mode": "visible",
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
            },
            "icon": None,
            "version_info": {},
            "schema_version": 1,
        },
    }

    result = BuildResult.from_dict(payload)
    assert result.output_exe == Path("dist/demo.exe")
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

    build_result = bat2pe_module.build(
        input_script=script,
        output_exe=output,
        icon=icon,
        company="Acme",
        product="Functional API",
    )

    assert build_result.output_exe == output
    inspect_result = bat2pe_module.inspect(output)
    assert inspect_result.source_script_name == "functional_api.bat"
    verify_result = bat2pe_module.verify(script, output)
    assert verify_result.success is True


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
        input_script=script,
        output_exe=output,
        window="visible",
        icon=icon,
        company="Acme",
        product="Runner",
        description="Python API test",
        file_version="2.3.4",
        product_version="5.6.7",
        original_filename="python_api.exe",
        internal_name="python_api",
    )
    build_result = builder.build()

    assert build_result.output_exe == output
    assert build_result.stub_path.name == "bat2pe-stub-console.exe"
    assert build_result.script_encoding == "utf8"
    assert build_result.inspect.source_extension == ".cmd"
    assert build_result.inspect.version_info.company_name == "Acme"
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


def test_python_api_raises_typed_errors(
    bat2pe_module,
    test_dir: Path,
) -> None:
    bad_script = test_dir / "bad.txt"
    bad_script.write_text("not a script", encoding="utf-8")
    output = test_dir / "bad.exe"

    with pytest.raises(bat2pe_module.BuildError) as build_error:
        bat2pe_module.Builder(
            input_script=bad_script,
            output_exe=output,
        ).build()

    assert build_error.value.code == 101
    assert build_error.value.path == bad_script

    unsupported = test_dir / "unsupported.bat"
    unsupported.write_bytes(b"@\x00e\x00c\x00h\x00o\x00")
    with pytest.raises(bat2pe_module.BuildError) as unsupported_error:
        bat2pe_module.Builder(
            input_script=unsupported,
            output_exe=test_dir / "unsupported.exe",
        ).build()

    assert unsupported_error.value.code == 102
    assert unsupported_error.value.path is None

    invalid_exe = test_dir / "invalid.exe"
    invalid_exe.write_text("plain text", encoding="utf-8")
    with pytest.raises(bat2pe_module.InspectError) as inspect_error:
        bat2pe_module.Inspector(invalid_exe).inspect()

    assert inspect_error.value.code == 104
    assert inspect_error.value.path is None


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
            input_script=script,
            output_exe=test_dir / "missing_stubs.exe",
        )

    assert build_error.value.code == 103
