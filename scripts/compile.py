#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_PACKAGE_DIR = REPO_ROOT / "python" / "bat2pe"
PYTHON_BIN_DIR = PYTHON_PACKAGE_DIR / "bin"


@dataclass(frozen=True)
class BuildLayout:
    profile: str
    target_dir: Path

    @property
    def output_dir(self) -> Path:
        return self.target_dir / self.profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build all bat2pe artifacts, defaulting to release-grade outputs for "
            "the Rust CLI, runtime stubs, and the Python native extension."
        )
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Build debug artifacts instead of the default release artifacts.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=REPO_ROOT / "target",
        help="Cargo target directory to use. Defaults to the repository target/ directory.",
    )
    parser.add_argument(
        "--skip-copy",
        action="store_true",
        help="Only build artifacts and skip syncing them into python/bat2pe/.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path = REPO_ROOT) -> None:
    printable = " ".join(command)
    print(f"[compile] {printable}")
    subprocess.run(command, cwd=cwd, check=True)


def ensure_windows() -> None:
    if os.name != "nt":
        raise SystemExit("compile.py currently targets Windows only.")


def build_all(layout: BuildLayout) -> None:
    command = [
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
        str(layout.target_dir),
    ]
    if layout.profile == "release":
        command.append("--release")
    run(command)


def extension_suffix() -> str:
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise SystemExit("failed to resolve Python extension suffix for the current interpreter")
    return suffix


def sync_artifacts(layout: BuildLayout) -> dict[str, str]:
    output_dir = layout.output_dir
    PYTHON_BIN_DIR.mkdir(parents=True, exist_ok=True)

    cli_exe = output_dir / "bat2pe.exe"
    stub_console = output_dir / "bat2pe-stub-console.exe"
    stub_windows = output_dir / "bat2pe-stub-windows.exe"
    native_dll = output_dir / "bat2pe_py.dll"

    expected = [cli_exe, stub_console, stub_windows, native_dll]
    missing = [path for path in expected if not path.exists()]
    if missing:
        formatted = "\n".join(str(path) for path in missing)
        raise SystemExit(f"missing build artifacts after cargo build:\n{formatted}")

    for old_pyd in PYTHON_PACKAGE_DIR.glob("_native*.pyd"):
        old_pyd.unlink()

    python_native = PYTHON_PACKAGE_DIR / f"_native{extension_suffix()}"
    shutil.copy2(native_dll, python_native)
    shutil.copy2(stub_console, PYTHON_BIN_DIR / stub_console.name)
    shutil.copy2(stub_windows, PYTHON_BIN_DIR / stub_windows.name)

    return {
        "cli_exe_built": str(cli_exe),
        "python_native": str(python_native),
        "stub_console_exe": str(PYTHON_BIN_DIR / stub_console.name),
        "stub_windows_exe": str(PYTHON_BIN_DIR / stub_windows.name),
    }


def main() -> int:
    ensure_windows()
    args = parse_args()
    layout = BuildLayout(
        profile="debug" if args.debug else "release",
        target_dir=args.target_dir.resolve(),
    )

    build_all(layout)

    summary: dict[str, str] = {
        "profile": layout.profile,
        "target_dir": str(layout.target_dir),
        "output_dir": str(layout.output_dir),
    }
    if args.skip_copy:
        print(json.dumps(summary, indent=2))
        return 0

    summary.update(sync_artifacts(layout))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
