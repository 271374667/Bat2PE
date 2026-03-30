from __future__ import annotations

import subprocess
import time
from pathlib import Path


def _wait_until(
    predicate,
    *,
    timeout_seconds: float = 10.0,
    poll_interval_seconds: float = 0.1,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll_interval_seconds)

    assert predicate()


def test_runtime_preserves_dp0_and_cleans_temp_script(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "dp0_report.cmd"
    script.write_bytes(b"@echo off\r\necho dp0=%~dp0 1>&2\r\nexit /b 0\r\n")
    output = test_dir / "dp0_report.exe"

    build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
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
    assert list(output.parent.glob("bat2pe-*.bat")) == []


def test_runtime_keeps_caller_working_directory(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "cwd_report.bat"
    script.write_bytes(b"@echo off\r\necho cwd=%CD% 1>&2\r\nexit /b 0\r\n")
    output = test_dir / "cwd_report.exe"

    build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
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

    build = cli_runner(
        "build",
        "--input-bat-path",
        script,
        "--output-exe-path",
        output,
        "--visible",
        "false",
    )
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
    assert list(output.parent.glob("bat2pe-*.bat")) == []


def test_runtime_cleans_legacy_stale_bat_files_on_startup(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "cleanup_legacy.cmd"
    script.write_bytes(b"@echo off\r\nexit /b 0\r\n")
    output = test_dir / "cleanup_legacy.exe"

    build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
    assert build.returncode == 0, build.stderr

    stale_bat = output.parent / "bat2pe-4294967294-1.bat"
    stale_cmd = output.parent / "bat2pe-4294967294-2.cmd"
    stale_bat.write_bytes(b"@echo off\r\n")
    stale_cmd.write_bytes(b"@echo off\r\n")

    completed = subprocess.run(
        [str(output)],
        cwd=test_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert not stale_bat.exists()
    assert not stale_cmd.exists()
    assert list(output.parent.glob("bat2pe-*.cmd")) == []
    assert list(output.parent.glob("bat2pe-*.bat")) == []


def test_runtime_cleans_temp_script_after_forced_termination(
    cli_runner,
    test_dir: Path,
) -> None:
    script = test_dir / "forced_cleanup.bat"
    script.write_bytes(
        b"@echo off\r\n"
        b"echo started>\"%~dp0forced_cleanup_started.txt\"\r\n"
        b"ping -n 6 127.0.0.1 >nul\r\n"
        b"exit /b 0\r\n"
    )
    output = test_dir / "forced_cleanup.exe"

    build = cli_runner("build", "--input-bat-path", script, "--output-exe-path", output)
    assert build.returncode == 0, build.stderr

    process = subprocess.Popen(
        [str(output)],
        cwd=test_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_until(lambda: bool(list(output.parent.glob("bat2pe-*.cmd"))))
        temp_scripts = list(output.parent.glob("bat2pe-*.cmd"))
        assert len(temp_scripts) == 1
        assert list(output.parent.glob("bat2pe-*.bat")) == []

        process.kill()
        process.wait(timeout=5)

        _wait_until(
            lambda: not list(output.parent.glob("bat2pe-*.cmd")),
            timeout_seconds=15.0,
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert (test_dir / "forced_cleanup_started.txt").exists()
    assert list(output.parent.glob("bat2pe-*.cmd")) == []
    assert list(output.parent.glob("bat2pe-*.bat")) == []
