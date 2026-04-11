from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def load_change_version_module(repo_root: Path) -> ModuleType:
    path = repo_root / "scripts" / "change_version.py"
    spec = importlib.util.spec_from_file_location("bat2pe_change_version_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load scripts/change_version.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_change_version_script_normalizes_semver(repo_root: Path) -> None:
    module = load_change_version_module(repo_root)

    assert module.normalize_version("1.2.3") == "1.2.3"
    assert module.normalize_version("v1.2.3") == "1.2.3"


def test_change_version_script_rejects_invalid_semver(repo_root: Path) -> None:
    module = load_change_version_module(repo_root)

    try:
        module.normalize_version("1.2")
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid version to raise ValueError")


def test_change_version_script_updates_manifests_and_lockfiles(
    repo_root: Path,
    test_dir: Path,
) -> None:
    module = load_change_version_module(repo_root)

    fake_repo = test_dir / "repo"
    (fake_repo / "crates" / "bat2pe-cli").mkdir(parents=True)
    (fake_repo / "crates" / "bat2pe-core").mkdir(parents=True)
    (fake_repo / "crates" / "bat2pe-py").mkdir(parents=True)

    (fake_repo / "Cargo.toml").write_text(
        "\n".join(
            [
                "[workspace]",
                'members = [',
                '  "crates/bat2pe-core",',
                '  "crates/bat2pe-cli",',
                '  "crates/bat2pe-py",',
                "]",
                "",
                "[workspace.package]",
                'version = "0.1.0"',
                "",
                "[workspace.dependencies]",
                'serde = "1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "bat2pe"',
                'version = "1.7.3"',
                'requires-python = ">=3.10"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "Cargo.lock").write_text(
        "\n".join(
            [
                "version = 4",
                "",
                "[[package]]",
                'name = "bat2pe"',
                'version = "0.1.0"',
                "",
                "[[package]]",
                'name = "bat2pe-core"',
                'version = "0.1.0"',
                "",
                "[[package]]",
                'name = "bat2pe-py"',
                'version = "0.1.0"',
                "",
                "[[package]]",
                'name = "serde"',
                'version = "1.0.228"',
                'source = "registry+https://github.com/rust-lang/crates.io-index"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "uv.lock").write_text(
        "\n".join(
            [
                "version = 1",
                'requires-python = ">=3.10"',
                "",
                "[[package]]",
                'name = "bat2pe"',
                'version = "0.1.0"',
                'source = { editable = "." }',
                "",
                "[[package]]",
                'name = "pytest"',
                'version = "8.4.2"',
                'source = { registry = "https://pypi.org/simple" }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "crates" / "bat2pe-cli" / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "bat2pe"',
                "version.workspace = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "crates" / "bat2pe-core" / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "bat2pe-core"',
                "version.workspace = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "crates" / "bat2pe-py" / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "bat2pe-py"',
                "version.workspace = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    summary = module.change_version(fake_repo, "v2.3.4")

    cargo_toml = (fake_repo / "Cargo.toml").read_text(encoding="utf-8")
    pyproject = (fake_repo / "pyproject.toml").read_text(encoding="utf-8")
    cargo_lock = (fake_repo / "Cargo.lock").read_text(encoding="utf-8")
    uv_lock = (fake_repo / "uv.lock").read_text(encoding="utf-8")

    assert summary["version"] == "2.3.4"
    assert summary["tag"] == "v2.3.4"
    assert summary["project_name"] == "bat2pe"
    assert summary["workspace_packages"] == ["bat2pe-core", "bat2pe", "bat2pe-py"]
    assert summary["changed_files"] == ["Cargo.toml", "pyproject.toml", "Cargo.lock", "uv.lock"]
    assert 'version = "2.3.4"' in cargo_toml
    assert 'version = "2.3.4"' in pyproject
    assert 'name = "bat2pe"\nversion = "2.3.4"' in cargo_lock
    assert 'name = "bat2pe-core"\nversion = "2.3.4"' in cargo_lock
    assert 'name = "bat2pe-py"\nversion = "2.3.4"' in cargo_lock
    assert 'name = "serde"\nversion = "1.0.228"' in cargo_lock
    assert cargo_lock.startswith("version = 4\n\n")
    assert 'name = "bat2pe"\nversion = "2.3.4"\nsource = { editable = "." }' in uv_lock
    assert 'name = "pytest"\nversion = "8.4.2"' in uv_lock
    assert uv_lock.startswith('version = 1\nrequires-python = ">=3.10"\n\n')


def test_change_version_script_main_reports_json_summary(
    repo_root: Path,
    test_dir: Path,
    monkeypatch,
    capsys,
) -> None:
    module = load_change_version_module(repo_root)

    fake_repo = test_dir / "repo"
    (fake_repo / "crates" / "bat2pe-cli").mkdir(parents=True)

    (fake_repo / "Cargo.toml").write_text(
        "\n".join(
            [
                "[workspace]",
                'members = [',
                '  "crates/bat2pe-cli",',
                "]",
                "",
                "[workspace.package]",
                'version = "0.1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "bat2pe"',
                'version = "0.1.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (fake_repo / "crates" / "bat2pe-cli" / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "bat2pe"',
                "version.workspace = true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(module, "TARGET_VERSION", "3.4.5")

    result = module.main()

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert result == 0
    assert summary["version"] == "3.4.5"
    assert summary["tag"] == "v3.4.5"
    assert summary["changed_files"] == ["Cargo.toml", "pyproject.toml"]
