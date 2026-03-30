from __future__ import annotations

import codecs
import ctypes
import json
from pathlib import Path


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


def test_cli_help_smoke(cli_runner) -> None:
    completed = cli_runner("--help")
    help_subcommand = cli_runner("help")
    version = cli_runner("--version")
    version_subcommand = cli_runner("version")

    assert completed.returncode == 0
    assert help_subcommand.returncode == 0
    assert version.returncode == 0
    assert version_subcommand.returncode == 0
    assert help_subcommand.stdout == completed.stdout
    assert version_subcommand.stdout == version.stdout
    assert "Bat2PE CLI" in completed.stdout
    assert 'Use "bat2pe <COMMAND> --help" for command-specific help.' in completed.stdout
    assert "bat2pe build run.bat" in completed.stdout
    assert "bat2pe verify run.bat run.exe" in completed.stdout
    assert version.stdout.startswith("bat2pe ")


def test_cli_subcommand_help_is_specific(cli_runner) -> None:
    build_help = cli_runner("build", "--help")
    assert build_help.returncode == 0
    assert "Build Command" in build_help.stdout
    assert "--input-bat-path PATH" in build_help.stdout
    assert "--visible BOOL" in build_help.stdout
    assert "build run.bat --visible false" in build_help.stdout

    build_help_alias = cli_runner("help", "build")
    assert build_help_alias.returncode == 0
    assert build_help_alias.stdout == build_help.stdout

    inspect_help = cli_runner("inspect", "--help")
    assert inspect_help.returncode == 0
    assert "Inspect Command" in inspect_help.stdout
    assert "--exe-path PATH" in inspect_help.stdout
    assert "--input-bat-path PATH" not in inspect_help.stdout

    verify_help = cli_runner("verify", "--help")
    assert verify_help.returncode == 0
    assert "Verify Command" in verify_help.stdout
    assert "<SCRIPT_PATH>" in verify_help.stdout
    assert "--script-path, --script PATH" in verify_help.stdout
    assert "--arg VALUE" in verify_help.stdout


def test_cli_reports_usage_errors(cli_runner, test_dir: Path) -> None:
    missing_input = cli_runner("build")
    assert missing_input.returncode == 1
    assert "missing input bat path" in missing_input.stderr

    inspect_missing = cli_runner("inspect")
    assert inspect_missing.returncode == 1
    assert "missing executable path" in inspect_missing.stderr

    verify_missing = cli_runner("verify", "--script-path", test_dir / "missing.bat")
    assert verify_missing.returncode == 1
    assert "missing executable path" in verify_missing.stderr

    verify_positional = cli_runner("verify", "positional")
    assert verify_positional.returncode == 1
    assert "missing executable path" in verify_positional.stderr

    missing_out_value = cli_runner(
        "build",
        "--input-bat-path",
        test_dir / "missing.bat",
        "--output-exe-path",
    )
    assert missing_out_value.returncode == 1
    assert "missing value for --output-exe-path" in missing_out_value.stderr


def test_cli_build_and_inspect_returns_full_metadata(
    cli_runner,
    fake_ico_bytes: bytes,
    test_dir: Path,
) -> None:
    script = test_dir / "launcher.bat"
    script_bytes = b"@echo off\r\necho cli metadata 1>&2\r\nexit /b 0\r\n"
    script.write_bytes(script_bytes)

    icon = test_dir / "sample.ico"
    icon.write_bytes(fake_ico_bytes)

    output = test_dir / "launcher.exe"
    build = cli_runner(
        "build",
        "--input-bat-path",
        script,
        "--output-exe-path",
        output,
        "--icon-path",
        icon,
        "--company",
        "Acme",
        "--product",
        "Runner",
        "--description",
        "CLI metadata test",
        "--file-version",
        "1.2.3",
        "--product-version",
        "4.5.6",
        "--original-filename",
        "launcher.exe",
        "--internal-name",
        "launcher",
        "--visible",
        "false",
        "--uac",
    )

    assert build.returncode == 0, build.stderr
    payload = json.loads(build.stdout)
    assert Path(payload["output_exe_path"]) == output
    assert payload["window_mode"] == "hidden"
    assert payload["uac"] is True
    assert payload["script_encoding"] == "utf8"
    assert payload["inspect"]["source_extension"] == ".bat"
    assert payload["inspect"]["script_length"] == len(script_bytes)
    assert payload["inspect"]["runtime"]["window_mode"] == "hidden"
    assert payload["inspect"]["runtime"]["uac"] is True
    assert payload["inspect"]["icon"]["file_name"] == "sample.ico"
    assert payload["inspect"]["version_info"]["company_name"] == "Acme"
    assert payload["inspect"]["version_info"]["file_version"] == {
        "major": 1,
        "minor": 2,
        "patch": 3,
    }
    assert payload["inspect"]["version_info"]["product_version"] == {
        "major": 4,
        "minor": 5,
        "patch": 6,
    }
    assert _read_version_string(output, "CompanyName") == "Acme"
    assert _read_version_string(output, "ProductName") == "Runner"
    assert _read_version_string(output, "FileDescription") == "CLI metadata test"
    assert _read_version_string(output, "OriginalFilename") == "launcher.exe"
    assert _read_version_string(output, "InternalName") == "launcher"
    assert _read_version_string(output, "FileVersion") == "1.2.3"
    assert _read_version_string(output, "ProductVersion") == "4.5.6"
    assert _read_fixed_version(output, "file") == (1, 2, 3)
    assert _read_fixed_version(output, "product") == (4, 5, 6)

    inspect = cli_runner("inspect", "--exe-path", output)
    assert inspect.returncode == 0, inspect.stderr
    inspect_payload = json.loads(inspect.stdout)
    assert inspect_payload["source_script_name"] == "launcher.bat"
    assert inspect_payload["schema_version"] == 1
    assert inspect_payload["icon"]["size"] == len(fake_ico_bytes)
    assert inspect_payload["runtime"]["uac"] is True
    assert _extract_icon_count(output) > 0
    assert _extract_execution_level(output) == "requireAdministrator"


def test_cli_supports_overwrite_and_quiet_mode(cli_runner, test_dir: Path) -> None:
    script = test_dir / "overwrite.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "overwrite.exe"

    first = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
    assert first.returncode == 0, first.stderr
    assert output.exists()

    second = cli_runner(
        "build",
        "--input-bat-path",
        script,
        "--output-exe-path",
        output,
        "--quiet",
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout == ""
    assert output.exists()

    inspect_quiet = cli_runner("inspect", "--exe-path", output, "--quiet")
    assert inspect_quiet.returncode == 0, inspect_quiet.stderr
    assert inspect_quiet.stdout == ""


def test_cli_defaults_output_path_and_overwrites_existing_file(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "default_output.cmd"
    script.write_bytes(b"@echo off\r\necho default path 1>&2\r\nexit /b 0\r\n")
    default_output = test_dir / "default_output.exe"
    default_output.write_text("stale exe placeholder", encoding="utf-8")

    build = cli_runner("build", "--input-bat-path", script)

    assert build.returncode == 0, build.stderr
    payload = json.loads(build.stdout)
    assert Path(payload["output_exe_path"]) == default_output
    assert default_output.exists()

    inspect = cli_runner("inspect", "--exe-path", default_output)
    assert inspect.returncode == 0, inspect.stderr
    inspect_payload = json.loads(inspect.stdout)
    assert inspect_payload["source_script_name"] == "default_output.cmd"
    assert inspect_payload["runtime"]["uac"] is False
    assert inspect_payload["version_info"]["original_filename"] == "default_output.exe"
    assert inspect_payload["version_info"]["internal_name"] == "default_output"
    assert _read_version_string(default_output, "OriginalFilename") == "default_output.exe"
    assert _read_version_string(default_output, "InternalName") == "default_output"
    assert _extract_execution_level(default_output) == "asInvoker"


def test_cli_rejects_invalid_inputs(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "bad.txt"
    script.write_text("not a batch file", encoding="utf-8")
    output = test_dir / "bad.exe"

    bad_script = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
    assert bad_script.returncode == 1
    assert "input must end with .bat or .cmd" in bad_script.stderr

    empty_script = test_dir / "empty.bat"
    empty_script.write_bytes(b"")
    empty_result = cli_runner(
        "build",
        "--input-bat-path",
        empty_script,
        "--output-exe-path",
        output,
    )
    assert empty_result.returncode == 1
    assert "input script is empty" in empty_result.stderr

    real_script = test_dir / "real.bat"
    real_script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    bad_icon = test_dir / "icon.txt"
    bad_icon.write_text("not an icon", encoding="utf-8")
    bad_icon_result = cli_runner(
        "build",
        "--input-bat-path",
        real_script,
        "--output-exe-path",
        output,
        "--icon-path",
        bad_icon,
    )
    assert bad_icon_result.returncode == 1
    assert "only .ico icon files are supported" in bad_icon_result.stderr

    invalid_visible = cli_runner(
        "build",
        "--input-bat-path",
        real_script,
        "--output-exe-path",
        output,
        "--visible",
        "invalid",
    )
    assert invalid_visible.returncode == 1
    assert "--visible must be one of" in invalid_visible.stderr

    invalid_version = cli_runner(
        "build",
        "--input-bat-path",
        real_script,
        "--output-exe-path",
        output,
        "--file-version",
        "1.2.3.4",
    )
    assert invalid_version.returncode == 1
    assert "major.minor.patch" in invalid_version.stderr

    invalid_exe = test_dir / "invalid.exe"
    invalid_exe.write_text("plain text", encoding="utf-8")
    inspect = cli_runner("inspect", "--exe-path", invalid_exe)
    assert inspect.returncode == 1
    assert "failed to load executable as a data file" in inspect.stderr


def test_cli_verify_matches_args_and_working_directory(cli_runner, test_dir: Path) -> None:
    script = test_dir / "args_and_cwd.bat"
    script.write_bytes(
        b"@echo off\r\n"
        b"echo cwd=%CD% 1>&2\r\n"
        b"echo arg1=%~1 1>&2\r\n"
        b"echo arg2=%~2 1>&2\r\n"
        b"exit /b 7\r\n"
    )
    output = test_dir / "args_and_cwd.exe"
    build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
    assert build.returncode == 0, build.stderr

    working_dir = test_dir / "runtime cwd"
    working_dir.mkdir()
    verify = cli_runner(
        "verify",
        script,
        output,
        "--cwd-path",
        working_dir,
        "--arg",
        "alpha beta",
        "--arg",
        "gamma",
    )

    assert verify.returncode == 0, verify.stderr
    payload = json.loads(verify.stdout)
    assert payload["success"] is True
    assert payload["exit_code_match"] is True
    assert payload["stderr_match"] is True
    assert payload["script"]["exit_code"] == 7
    assert f"cwd={working_dir}" in payload["script"]["stderr"]
    assert "arg1=alpha beta" in payload["script"]["stderr"]
    assert "arg2=gamma" in payload["script"]["stderr"]

    quiet = cli_runner(
        "verify",
        script,
        output,
        "--cwd-path",
        working_dir,
        "--quiet",
    )
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout == ""


def test_cli_verify_reports_mismatch(cli_runner, test_dir: Path) -> None:
    built_script = test_dir / "built.bat"
    built_script.write_bytes(b"@echo off\r\necho built 1>&2\r\nexit /b 0\r\n")
    different_script = test_dir / "different.bat"
    different_script.write_bytes(b"@echo off\r\necho different 1>&2\r\nexit /b 5\r\n")
    output = test_dir / "mismatch.exe"

    build = cli_runner("build", "--input-bat-path", built_script, "--output-exe-path", output)
    assert build.returncode == 0, build.stderr

    verify = cli_runner("verify", different_script, output)
    assert verify.returncode == 1
    payload = json.loads(verify.stdout)
    assert payload["success"] is False
    assert payload["exit_code_match"] is False
    assert payload["stderr_match"] is False
    assert payload["script"]["exit_code"] == 5
    assert payload["executable"]["exit_code"] == 0


def test_cli_verify_rejects_uac_enabled_executable(cli_runner, test_dir: Path) -> None:
    script = test_dir / "uac_verify.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "uac_verify.exe"

    build = cli_runner(
        "build",
        "--input-bat-path",
        script,
        "--output-exe-path",
        output,
        "--uac",
    )
    assert build.returncode == 0, build.stderr

    verify = cli_runner("verify", script, output)
    assert verify.returncode == 1
    assert "does not support uac-enabled executables" in verify.stderr


def test_cli_records_supported_encodings(cli_runner, test_dir: Path) -> None:
    cases = [
        (
            "utf8bom.bat",
            codecs.BOM_UTF8 + b"@echo off\r\nexit /b 0\r\n",
            "utf8_bom",
        ),
        (
            "utf16.cmd",
            codecs.BOM_UTF16_LE + "@echo off\r\nexit /b 0\r\n".encode("utf-16le"),
            "utf16_le_bom",
        ),
        (
            "gbk.bat",
            b"@echo off\r\nrem \xc4\xe3\xba\xc3\r\nexit /b 0\r\n",
            "ansi_gbk",
        ),
    ]

    for file_name, script_bytes, expected in cases:
        script = test_dir / file_name
        script.write_bytes(script_bytes)
        output = test_dir / f"{script.stem}.exe"
        build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
        assert build.returncode == 0, build.stderr

        inspect = cli_runner("inspect", "--exe-path", output)
        assert inspect.returncode == 0, inspect.stderr
        payload = json.loads(inspect.stdout)
        assert payload["script_encoding"] == expected


def test_cli_rejects_unsupported_encoding(cli_runner, test_dir: Path) -> None:
    script = test_dir / "unsupported.bat"
    script.write_bytes(b"@\x00e\x00c\x00h\x00o\x00")
    output = test_dir / "unsupported.exe"

    completed = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)

    assert completed.returncode == 1
    assert "unsupported script encoding" in completed.stderr
