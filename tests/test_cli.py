from __future__ import annotations

import codecs
import json
from pathlib import Path


def test_cli_help_smoke(cli_runner) -> None:
    completed = cli_runner("help")

    assert completed.returncode == 0
    assert "bat2pe build" in completed.stdout
    assert "bat2pe verify" in completed.stdout


def test_cli_reports_usage_errors(cli_runner, test_dir: Path) -> None:
    missing_input = cli_runner("build")
    assert missing_input.returncode == 1
    assert "missing input script" in missing_input.stderr

    inspect_missing = cli_runner("inspect")
    assert inspect_missing.returncode == 1
    assert "missing executable path" in inspect_missing.stderr

    verify_missing = cli_runner("verify", "--script", test_dir / "missing.bat")
    assert verify_missing.returncode == 1
    assert "missing --exe" in verify_missing.stderr

    verify_positional = cli_runner("verify", "positional")
    assert verify_positional.returncode == 1
    assert "verify only accepts named options" in verify_positional.stderr

    missing_out_value = cli_runner("build", test_dir / "missing.bat", "--out")
    assert missing_out_value.returncode == 1
    assert "missing value for --out" in missing_out_value.stderr


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
        script,
        "--out",
        output,
        "--icon",
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
        "--window",
        "hidden",
    )

    assert build.returncode == 0, build.stderr
    payload = json.loads(build.stdout)
    assert Path(payload["output_exe"]) == output
    assert payload["window_mode"] == "hidden"
    assert payload["script_encoding"] == "utf8"
    assert payload["inspect"]["source_extension"] == ".bat"
    assert payload["inspect"]["script_length"] == len(script_bytes)
    assert payload["inspect"]["runtime"]["window_mode"] == "hidden"
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

    inspect = cli_runner("inspect", output)
    assert inspect.returncode == 0, inspect.stderr
    inspect_payload = json.loads(inspect.stdout)
    assert inspect_payload["source_script_name"] == "launcher.bat"
    assert inspect_payload["schema_version"] == 1
    assert inspect_payload["icon"]["size"] == len(fake_ico_bytes)


def test_cli_supports_overwrite_and_quiet_mode(cli_runner, test_dir: Path) -> None:
    script = test_dir / "overwrite.bat"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "overwrite.exe"

    first = cli_runner("build", script, "--out", output)
    assert first.returncode == 0, first.stderr
    assert output.exists()

    second = cli_runner("build", script, "--out", output, "--quiet")
    assert second.returncode == 0, second.stderr
    assert second.stdout == ""
    assert output.exists()

    inspect_quiet = cli_runner("inspect", output, "--quiet")
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

    build = cli_runner("build", script)

    assert build.returncode == 0, build.stderr
    payload = json.loads(build.stdout)
    assert Path(payload["output_exe"]) == default_output
    assert default_output.exists()

    inspect = cli_runner("inspect", default_output)
    assert inspect.returncode == 0, inspect.stderr
    inspect_payload = json.loads(inspect.stdout)
    assert inspect_payload["source_script_name"] == "default_output.cmd"


def test_cli_rejects_conflicting_verbosity_and_invalid_inputs(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "bad.txt"
    script.write_text("not a batch file", encoding="utf-8")
    output = test_dir / "bad.exe"

    conflict = cli_runner("build", script, "--out", output, "--quiet", "--verbose")
    assert conflict.returncode == 1
    assert "--quiet and --verbose are mutually exclusive" in conflict.stderr

    bad_script = cli_runner("build", script, "--out", output)
    assert bad_script.returncode == 1
    assert "input must end with .bat or .cmd" in bad_script.stderr

    empty_script = test_dir / "empty.bat"
    empty_script.write_bytes(b"")
    empty_result = cli_runner("build", empty_script, "--out", output)
    assert empty_result.returncode == 1
    assert "input script is empty" in empty_result.stderr

    real_script = test_dir / "real.bat"
    real_script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    bad_icon = test_dir / "icon.txt"
    bad_icon.write_text("not an icon", encoding="utf-8")
    bad_icon_result = cli_runner("build", real_script, "--out", output, "--icon", bad_icon)
    assert bad_icon_result.returncode == 1
    assert "only .ico icon files are supported" in bad_icon_result.stderr

    invalid_window = cli_runner(
        "build",
        real_script,
        "--out",
        output,
        "--window",
        "invalid",
    )
    assert invalid_window.returncode == 1
    assert "unsupported window mode" in invalid_window.stderr

    invalid_version = cli_runner(
        "build",
        real_script,
        "--out",
        output,
        "--file-version",
        "1.2.3.4",
    )
    assert invalid_version.returncode == 1
    assert "major.minor.patch" in invalid_version.stderr

    invalid_exe = test_dir / "invalid.exe"
    invalid_exe.write_text("plain text", encoding="utf-8")
    inspect = cli_runner("inspect", invalid_exe)
    assert inspect.returncode == 1
    assert "bat2pe overlay" in inspect.stderr


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
    build = cli_runner("build", script, "--out", output)
    assert build.returncode == 0, build.stderr

    working_dir = test_dir / "runtime cwd"
    working_dir.mkdir()
    verify = cli_runner(
        "verify",
        "--script",
        script,
        "--exe",
        output,
        "--cwd",
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
        "--script",
        script,
        "--exe",
        output,
        "--cwd",
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

    build = cli_runner("build", built_script, "--out", output)
    assert build.returncode == 0, build.stderr

    verify = cli_runner("verify", "--script", different_script, "--exe", output)
    assert verify.returncode == 1
    payload = json.loads(verify.stdout)
    assert payload["success"] is False
    assert payload["exit_code_match"] is False
    assert payload["stderr_match"] is False
    assert payload["script"]["exit_code"] == 5
    assert payload["executable"]["exit_code"] == 0


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
        build = cli_runner("build", script, "--out", output)
        assert build.returncode == 0, build.stderr

        inspect = cli_runner("inspect", output)
        assert inspect.returncode == 0, inspect.stderr
        payload = json.loads(inspect.stdout)
        assert payload["script_encoding"] == expected


def test_cli_rejects_unsupported_encoding(cli_runner, test_dir: Path) -> None:
    script = test_dir / "unsupported.bat"
    script.write_bytes(b"@\x00e\x00c\x00h\x00o\x00")
    output = test_dir / "unsupported.exe"

    completed = cli_runner("build", script, "--out", output)

    assert completed.returncode == 1
    assert "unsupported script encoding" in completed.stderr
