#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
# Edit this constant, then run this script directly.
TARGET_VERSION = "1.7.3"
SEMVER_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
PACKAGE_BLOCK_PATTERN = re.compile(r"(?ms)^\[\[package\]\]\n.*?(?=^\[\[package\]\]\n|\Z)")


def normalize_version(raw_value: str) -> str:
    value = raw_value.strip()
    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("version must use X.Y.Z or vX.Y.Z")
    return ".".join(match.groups())


def load_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"missing required file: {path}")
    return path.read_text(encoding="utf-8")


def store_text(path: Path, text: str) -> bool:
    previous = load_text(path)
    if previous == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def extract_section_body(text: str, section_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\[{re.escape(section_name)}\]\n(?P<body>.*?)(?=^\[|\Z)"
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"missing [{section_name}] section")
    return match.group("body")


def replace_section_version(text: str, section_name: str, version: str) -> str:
    pattern = re.compile(
        rf"(?ms)^(?P<header>\[{re.escape(section_name)}\]\n)(?P<body>.*?)(?=^\[|\Z)"
    )
    match = pattern.search(text)
    if match is None:
        raise ValueError(f"missing [{section_name}] section")

    body = match.group("body")
    updated_body, replacements = re.subn(
        r'(?m)^version = "[^"]+"$',
        f'version = "{version}"',
        body,
        count=1,
    )
    if replacements != 1:
        raise ValueError(f"missing version field in [{section_name}] section")

    return (
        text[: match.start("body")]
        + updated_body
        + text[match.end("body") :]
    )


def extract_project_name(pyproject_text: str) -> str:
    project_body = extract_section_body(pyproject_text, "project")
    match = re.search(r'(?m)^name = "([^"]+)"$', project_body)
    if match is None:
        raise ValueError("missing name field in [project] section")
    return match.group(1)


def extract_workspace_member_paths(workspace_text: str) -> list[str]:
    workspace_body = extract_section_body(workspace_text, "workspace")
    match = re.search(r"(?ms)^members = \[(?P<body>.*?)^\]", workspace_body)
    if match is None:
        raise ValueError("missing members list in [workspace] section")
    return re.findall(r'"([^"]+)"', match.group("body"))


def extract_package_name(manifest_text: str, manifest_path: Path) -> str:
    package_body = extract_section_body(manifest_text, "package")
    match = re.search(r'(?m)^name = "([^"]+)"$', package_body)
    if match is None:
        raise ValueError(f"missing package name in {manifest_path}")
    return match.group(1)


def workspace_package_names(repo_root: Path, workspace_text: str) -> list[str]:
    names: list[str] = []
    for member_path in extract_workspace_member_paths(workspace_text):
        manifest_path = repo_root / member_path / "Cargo.toml"
        manifest_text = load_text(manifest_path)
        names.append(extract_package_name(manifest_text, manifest_path))
    return names


def map_package_blocks(
    text: str,
    transform: Callable[[str], str],
) -> str:
    parts: list[str] = []
    last_end = 0
    for match in PACKAGE_BLOCK_PATTERN.finditer(text):
        parts.append(text[last_end : match.start()])
        parts.append(transform(match.group(0)))
        last_end = match.end()
    parts.append(text[last_end:])
    return "".join(parts)


def replace_cargo_lock_versions(
    cargo_lock_text: str,
    package_names: list[str],
    version: str,
) -> str:
    names = set(package_names)
    replacements = 0

    def update_block(block: str) -> str:
        nonlocal replacements
        name_match = re.search(r'(?m)^name = "([^"]+)"$', block)
        if name_match is None or name_match.group(1) not in names:
            return block
        if re.search(r'(?m)^source = ', block) is not None:
            return block

        updated_block, count = re.subn(
            r'(?m)^version = "[^"]+"$',
            f'version = "{version}"',
            block,
            count=1,
        )
        if count != 1:
            raise ValueError(f"missing version field in Cargo.lock block for {name_match.group(1)}")
        replacements += 1
        return updated_block

    updated_text = map_package_blocks(cargo_lock_text, update_block)

    if replacements == 0:
        raise ValueError("found Cargo.lock but no local workspace package blocks to update")

    return updated_text


def replace_uv_lock_version(uv_lock_text: str, package_name: str, version: str) -> str:
    replaced = False

    def update_block(block: str) -> str:
        nonlocal replaced
        name_match = re.search(r'(?m)^name = "([^"]+)"$', block)
        if name_match is None or name_match.group(1) != package_name:
            return block
        if 'source = { editable = "." }' not in block:
            return block

        updated_block, count = re.subn(
            r'(?m)^version = "[^"]+"$',
            f'version = "{version}"',
            block,
            count=1,
        )
        if count != 1:
            raise ValueError(f"missing version field in uv.lock block for {package_name}")
        replaced = True
        return updated_block

    updated_text = map_package_blocks(uv_lock_text, update_block)
    if not replaced:
        raise ValueError(
            f"found uv.lock but no editable package block for {package_name}"
        )
    return updated_text


def change_version(repo_root: Path, version: str) -> dict[str, object]:
    repo_root = repo_root.resolve()
    normalized_version = normalize_version(version)

    cargo_toml_path = repo_root / "Cargo.toml"
    pyproject_path = repo_root / "pyproject.toml"
    cargo_lock_path = repo_root / "Cargo.lock"
    uv_lock_path = repo_root / "uv.lock"

    cargo_toml_text = load_text(cargo_toml_path)
    pyproject_text = load_text(pyproject_path)

    project_name = extract_project_name(pyproject_text)
    cargo_package_names = workspace_package_names(repo_root, cargo_toml_text)

    checked_files = [
        str(cargo_toml_path.relative_to(repo_root)),
        str(pyproject_path.relative_to(repo_root)),
    ]
    changed_files: list[str] = []

    if store_text(
        cargo_toml_path,
        replace_section_version(cargo_toml_text, "workspace.package", normalized_version),
    ):
        changed_files.append(str(cargo_toml_path.relative_to(repo_root)))

    if store_text(
        pyproject_path,
        replace_section_version(pyproject_text, "project", normalized_version),
    ):
        changed_files.append(str(pyproject_path.relative_to(repo_root)))

    if cargo_lock_path.exists():
        checked_files.append(str(cargo_lock_path.relative_to(repo_root)))
        cargo_lock_text = load_text(cargo_lock_path)
        if store_text(
            cargo_lock_path,
            replace_cargo_lock_versions(cargo_lock_text, cargo_package_names, normalized_version),
        ):
            changed_files.append(str(cargo_lock_path.relative_to(repo_root)))

    if uv_lock_path.exists():
        checked_files.append(str(uv_lock_path.relative_to(repo_root)))
        uv_lock_text = load_text(uv_lock_path)
        if store_text(
            uv_lock_path,
            replace_uv_lock_version(uv_lock_text, project_name, normalized_version),
        ):
            changed_files.append(str(uv_lock_path.relative_to(repo_root)))

    return {
        "repo_root": str(repo_root),
        "version": normalized_version,
        "tag": f"v{normalized_version}",
        "project_name": project_name,
        "workspace_packages": cargo_package_names,
        "checked_files": checked_files,
        "changed_files": changed_files,
    }


def main() -> int:
    try:
        summary = change_version(REPO_ROOT, TARGET_VERSION)
    except (FileNotFoundError, ValueError) as error:
        print(f"change_version.py: {error}", file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
