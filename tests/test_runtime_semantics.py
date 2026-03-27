from __future__ import annotations

import subprocess
from pathlib import Path


def test_runtime_preserves_dp0_and_cleans_temp_script(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "dp0_report.cmd"
    script.write_bytes(b"@echo off\r\necho dp0=%~dp0 1>&2\r\nexit /b 0\r\n")
    output = test_dir / "dp0_report.exe"

    build = cli_runner("build", script, "--out", output)
    assert build.returncode == 0, build.stderr

    caller_cwd = test_dir / "caller cwd"
    caller_cwd.mkdir()
    completed = subprocess.run(
        [str(output)],
        cwd=caller_cwd,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr.strip() == f"dp0={output.parent}\\"
    assert list(output.parent.glob("bat2pe-*.cmd")) == []


def test_runtime_keeps_caller_working_directory(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "cwd_report.bat"
    script.write_bytes(b"@echo off\r\necho cwd=%CD% 1>&2\r\nexit /b 0\r\n")
    output = test_dir / "cwd_report.exe"

    build = cli_runner("build", script, "--out", output)
    assert build.returncode == 0, build.stderr

    caller_cwd = test_dir / "different cwd"
    caller_cwd.mkdir()
    completed = subprocess.run(
        [str(output)],
        cwd=caller_cwd,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stderr.strip() == f"cwd={caller_cwd}"


def test_hidden_runtime_preserves_exit_code(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "hidden_exit.bat"
    script.write_bytes(b"@echo off\r\necho hidden mode 1>&2\r\nexit /b 3\r\n")
    output = test_dir / "hidden_exit.exe"

    build = cli_runner("build", script, "--out", output, "--window", "hidden")
    assert build.returncode == 0, build.stderr

    completed = subprocess.run(
        [str(output)],
        cwd=test_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 3
    assert list(output.parent.glob("bat2pe-*.cmd")) == []
