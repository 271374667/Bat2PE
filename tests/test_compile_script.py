from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def load_compile_module(repo_root: Path) -> ModuleType:
    path = repo_root / "scripts" / "compile.py"
    spec = importlib.util.spec_from_file_location("bat2pe_compile_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load scripts/compile.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compile_script_defaults_to_release_profile(
    repo_root: Path,
    monkeypatch,
) -> None:
    module = load_compile_module(repo_root)
    monkeypatch.setattr("sys.argv", ["compile.py"])

    args = module.parse_args()

    assert args.debug is False


def test_compile_script_supports_debug_profile(
    repo_root: Path,
    monkeypatch,
) -> None:
    module = load_compile_module(repo_root)
    monkeypatch.setattr("sys.argv", ["compile.py", "--debug"])

    args = module.parse_args()

    assert args.debug is True


def test_compile_script_syncs_python_artifacts_with_single_host_executable(
    repo_root: Path,
    test_dir: Path,
    monkeypatch,
) -> None:
    module = load_compile_module(repo_root)

    package_dir = test_dir / "python" / "bat2pe"
    bin_dir = package_dir / "bin"
    package_dir.mkdir(parents=True)
    bin_dir.mkdir()
    unlinked: list[str] = []

    class PackageDirProxy:
        def __init__(self, path: Path) -> None:
            self.path = path

        def glob(self, pattern: str):
            class OldPyd:
                def unlink(self_nonlocal) -> None:
                    unlinked.append(pattern)

            return [OldPyd()]

        def __truediv__(self, other: str) -> Path:
            return self.path / other

    target_dir = test_dir / "target"
    output_dir = target_dir / "release"
    output_dir.mkdir(parents=True)
    cli_exe = output_dir / "bat2pe.exe"
    native_dll = output_dir / "bat2pe_py.dll"
    cli_exe.write_bytes(b"cli")
    native_dll.write_bytes(b"native")
    (bin_dir / "bat2pe-stub-console.exe").write_bytes(b"old-console")
    (bin_dir / "bat2pe-stub-windows.exe").write_bytes(b"old-windows")

    monkeypatch.setattr(module, "PYTHON_PACKAGE_DIR", PackageDirProxy(package_dir))
    monkeypatch.setattr(module, "PYTHON_BIN_DIR", bin_dir)

    summary = module.sync_artifacts(module.BuildLayout(profile="release", target_dir=target_dir))

    assert (package_dir / "_native.pyd").read_bytes() == b"native"
    assert (bin_dir / "bat2pe.exe").read_bytes() == b"cli"
    assert not (bin_dir / "bat2pe-stub-console.exe").exists()
    assert not (bin_dir / "bat2pe-stub-windows.exe").exists()
    assert unlinked == ["_native*.pyd"]
    assert summary["cli_exe_built"] == str(cli_exe)
    assert summary["python_native"] == str(package_dir / "_native.pyd")
    assert summary["python_cli_exe"] == str(bin_dir / "bat2pe.exe")


def test_compile_script_main_skip_copy_reports_release_summary(
    repo_root: Path,
    test_dir: Path,
    monkeypatch,
    capsys,
) -> None:
    module = load_compile_module(repo_root)
    target_dir = test_dir / "custom-target"
    calls: list[object] = []

    monkeypatch.setattr(module, "ensure_windows", lambda: None)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(debug=False, target_dir=target_dir, skip_copy=True),
    )
    monkeypatch.setattr(module, "build_all", lambda layout: calls.append(layout))

    result = module.main()

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert result == 0
    assert len(calls) == 1
    assert calls[0].profile == "release"
    assert summary["profile"] == "release"
    assert summary["output_dir"].endswith("release")


def test_compile_script_main_syncs_debug_summary(
    repo_root: Path,
    test_dir: Path,
    monkeypatch,
    capsys,
) -> None:
    module = load_compile_module(repo_root)
    target_dir = test_dir / "custom-target"
    calls: list[object] = []

    monkeypatch.setattr(module, "ensure_windows", lambda: None)
    monkeypatch.setattr(
        module,
        "parse_args",
        lambda: SimpleNamespace(debug=True, target_dir=target_dir, skip_copy=False),
    )
    monkeypatch.setattr(module, "build_all", lambda layout: calls.append(layout))
    monkeypatch.setattr(
        module,
        "sync_artifacts",
        lambda layout: {
            "python_native": "python/bat2pe/_native.pyd",
            "python_cli_exe": "python/bat2pe/bin/bat2pe.exe",
            "cli_exe_built": str(layout.output_dir / "bat2pe.exe"),
        },
    )

    result = module.main()

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert result == 0
    assert len(calls) == 1
    assert calls[0].profile == "debug"
    assert summary["profile"] == "debug"
    assert summary["python_native"].endswith("_native.pyd")
