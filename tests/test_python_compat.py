from __future__ import annotations

import ast
from pathlib import Path


def test_python_package_uses_python310_compatible_syntax(repo_root: Path) -> None:
    python_files = sorted((repo_root / "python").rglob("*.py"))
    script_files = sorted((repo_root / "scripts").rglob("*.py"))
    files = python_files + script_files

    assert files, "expected Python source files to exist"

    for path in files:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path), feature_version=(3, 10))
