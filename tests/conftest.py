from __future__ import annotations

import importlib
import os
import struct
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE_ROOT = REPO_ROOT / "python" / "bat2pe"
TEST_RUNTIME_PREFIX = "bat2pe-test-runtime-"
LEGACY_REPO_RUNTIME_PREFIX = f".{TEST_RUNTIME_PREFIX}"


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


def _remove_readonly(func, path: str, _exc_info: object) -> None:
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _cleanup_tree(path: Path) -> None:
    if not path.exists():
        return
    last_error: OSError | None = None
    for _ in range(50):
        try:
            shutil.rmtree(path, onerror=_remove_readonly)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            time.sleep(0.2)

    if last_error is not None:
        raise last_error


def _schedule_cleanup_after_exit(path: Path) -> None:
    cleanup_script = (
        "import pathlib\n"
        "import shutil\n"
        "import sys\n"
        "import time\n"
        "target = pathlib.Path(sys.argv[1])\n"
        "for _ in range(150):\n"
        "    try:\n"
        "        shutil.rmtree(target)\n"
        "        break\n"
        "    except FileNotFoundError:\n"
        "        break\n"
        "    except OSError:\n"
        "        time.sleep(0.2)\n"
    )
    creationflags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        creationflags |= getattr(subprocess, flag_name, 0)

    subprocess.Popen(
        [sys.executable, "-c", cleanup_script, str(path)],
        cwd=tempfile.gettempdir(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )


def _cleanup_legacy_repo_runtime_dirs() -> None:
    for candidate in REPO_ROOT.glob(f"{LEGACY_REPO_RUNTIME_PREFIX}*"):
        if not candidate.is_dir():
            continue
        try:
            _cleanup_tree(candidate)
        except OSError:
            _schedule_cleanup_after_exit(candidate)


@pytest.fixture(scope="session")
def session_temp_root() -> Iterator[Path]:
    _cleanup_legacy_repo_runtime_dirs()
    root = Path(tempfile.mkdtemp(prefix=TEST_RUNTIME_PREFIX))
    try:
        yield root
    finally:
        try:
            _cleanup_tree(root)
        except OSError:
            _schedule_cleanup_after_exit(root)


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
    def png_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        return (
            len(chunk_data).to_bytes(4, "big")
            + chunk_type
            + chunk_data
            + crc.to_bytes(4, "big")
        )

    png_signature = b"\x89PNG\r\n\x1a\n"
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    idat = png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
    iend = png_chunk(b"IEND", b"")
    png_bytes = png_signature + ihdr + idat + iend

    icon_header = struct.pack("<HHH", 0, 1, 1)
    icon_entry = struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, len(png_bytes), 22)
    return icon_header + icon_entry + png_bytes
