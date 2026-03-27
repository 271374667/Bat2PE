from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE_ROOT = REPO_ROOT / "python" / "bat2pe"


@dataclass(frozen=True)
class BuildArtifacts:
    repo_root: Path
    target_dir: Path
    profile_dir: Path
    cli_exe: Path
    stub_console_exe: Path
    stub_windows_exe: Path
    native_library: Path


def _run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(command)}\n"
            f"stdout:\n{_decode_output(completed.stdout)}\n"
            f"stderr:\n{_decode_output(completed.stderr)}"
        )
    return completed


def _decode_output(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def _reset_bat2pe_modules() -> None:
    for name in list(sys.modules):
        if name == "bat2pe" or name.startswith("bat2pe."):
            sys.modules.pop(name, None)


@pytest.fixture(scope="session")
def session_temp_root() -> Path:
    root = REPO_ROOT / f".bat2pe-test-runtime-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def build_artifacts(session_temp_root: Path) -> BuildArtifacts:
    if os.name != "nt":
        pytest.skip("bat2pe functional tests require Windows")

    target_dir = session_temp_root / "cargo-target"
    target_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "cargo",
            "build",
            "-p",
            "bat2pe",
            "-p",
            "bat2pe-stub-console",
            "-p",
            "bat2pe-stub-windows",
            "-p",
            "bat2pe-py",
            "--target-dir",
            str(target_dir),
        ]
    )

    profile_dir = target_dir / "debug"
    artifacts = BuildArtifacts(
        repo_root=REPO_ROOT,
        target_dir=target_dir,
        profile_dir=profile_dir,
        cli_exe=profile_dir / "bat2pe.exe",
        stub_console_exe=profile_dir / "bat2pe-stub-console.exe",
        stub_windows_exe=profile_dir / "bat2pe-stub-windows.exe",
        native_library=profile_dir / "bat2pe_py.dll",
    )

    missing = [path for path in artifacts.__dict__.values() if isinstance(path, Path) and path.name.endswith((".exe", ".dll")) and not path.exists()]
    if missing:
        formatted = "\n".join(str(path) for path in missing)
        raise RuntimeError(f"missing expected build artifacts:\n{formatted}")

    return artifacts


@pytest.fixture(scope="session")
def bat2pe_module(
    build_artifacts: BuildArtifacts,
    session_temp_root: Path,
):
    package_root = session_temp_root / "python-package"
    package_root.mkdir(parents=True, exist_ok=True)
    package_dir = package_root / "bat2pe"
    package_dir.mkdir()

    for source in PYTHON_PACKAGE_ROOT.iterdir():
        if source.name == "__pycache__":
            continue
        if source.suffix == ".pyd":
            continue
        if source.is_file():
            shutil.copy2(source, package_dir / source.name)

    native_target = package_dir / "_native.pyd"
    shutil.copy2(build_artifacts.native_library, native_target)

    sys.path.insert(0, str(package_root))
    os.environ["BAT2PE_STUB_CONSOLE"] = str(build_artifacts.stub_console_exe)
    os.environ["BAT2PE_STUB_WINDOWS"] = str(build_artifacts.stub_windows_exe)
    _reset_bat2pe_modules()
    importlib.invalidate_caches()

    try:
        module = importlib.import_module("bat2pe")
        yield module
    finally:
        _reset_bat2pe_modules()
        importlib.invalidate_caches()
        try:
            sys.path.remove(str(package_root))
        except ValueError:
            pass


@pytest.fixture()
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture()
def test_dir(session_temp_root: Path, request: pytest.FixtureRequest) -> Path:
    safe_name = request.node.name.replace("[", "_").replace("]", "_").replace(" ", "_")
    directory = session_temp_root / f"{safe_name}-{uuid.uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture()
def cli_runner(build_artifacts: BuildArtifacts):
    def run_cli(*args: str | os.PathLike[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        command = [str(build_artifacts.cli_exe), *(str(arg) for arg in args)]
        return subprocess.run(
            command,
            cwd=cwd or build_artifacts.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    return run_cli


@pytest.fixture()
def fake_ico_bytes() -> bytes:
    return b"\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00fake-icon"
