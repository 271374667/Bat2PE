# Python API

`bat2pe` exposes both object-oriented and functional Python entry points.

## Architecture

The Python package does not shell out to the Rust CLI for build, inspect, or
verify operations.

Instead, the integration is split into two parts:

- `bat2pe._native.pyd`
  the PyO3 native extension that Python imports directly, and which embeds its
  own private runtime-host executable bytes
- `bat2pe.exe`
  the standalone Rust CLI for end users

Python calls the native extension, and the native extension writes an embedded
private runtime-host executable to the output path, patches the PE subsystem
for the requested window mode, then writes the bat2pe payload resources into
the copied executable.

This means the Python wheel no longer depends on an external `bat2pe.exe`
file. The CLI and Python module can now be distributed independently.

The extension targets CPython `3.10+` through PyO3's stable ABI mode.

## Development Setup

Build the native extension into the current virtual environment:

```powershell
uv sync
uv run maturin develop
```

The repository also provides a helper that builds the Rust artifacts and syncs
the Python-facing files into `python/bat2pe/`:

```powershell
uv run python scripts/compile.py
```

That script builds:

- `bat2pe.exe`
- `bat2pe_py.dll`

And syncs this Python package artifact:

- `python/bat2pe/_native.pyd`

Use `--debug` only when you explicitly want an unoptimized Rust build:

```powershell
uv run python scripts/compile.py --debug
```

`bat2pe-py` builds its embedded runtime host during the Rust build through its
own Cargo `build.rs`. No additional Python-side template file is required.

## Imports

All public names can be imported from the top-level package:

```python
from bat2pe import (
    # Classes
    Builder,
    # Functional helpers
    build,
    # Result types
    BuildResult,
    InspectResult,
    # Nested model types
    IconInfo,
    RuntimeConfig,
    VersionInfo,
    VersionTriplet,
    # Exceptions
    Bat2PeError,
    BuildError,
    # Error code constants
    ERR_INVALID_INPUT,
    ERR_UNSUPPORTED_INPUT,
    ERR_UNSUPPORTED_ENCODING,
    ERR_RESOURCE_NOT_FOUND,
    ERR_INVALID_EXECUTABLE,
    ERR_DIRECTORY_NOT_WRITABLE,
    ERR_IO,
    ERR_CLI_USAGE,
    ERR_VERIFY_MISMATCH,
    ERR_VERIFY_UAC_INTERACTIVE,
)
```

## Object-Oriented Usage

```python
from pathlib import Path

from bat2pe import Builder

builder = Builder(
    input_bat_path=Path("run.bat"),
    output_exe_path=Path("run.exe"),
    uac=True,
    company_name="Acme",
    product_name="Runner",
    description="Batch launcher",
    file_version="1.2.3",
    product_version="1.2.3",
)
build_result = builder.build()
```

If `output_exe_path` is omitted, the builder writes a same-name `.exe` beside
the input script. Existing files are overwritten.

## Functional Usage

```python
from bat2pe import build

build_result = build(
    input_bat_path="run.bat",
    output_exe_path="run.exe",
    visible=True,
    uac=False,
)
```

## Results

Python result values are standard-library dataclasses:

- `BuildResult`

Nested typed objects include:

- `VersionInfo`
- `VersionTriplet` — supports `str()` to produce `"major.minor.patch"` format
- `RuntimeConfig`
- `IconInfo`
- `InspectResult` — embedded in `BuildResult.inspect`

`BuildResult` exposes:

- `output_exe_path`
- `template_executable_path`
- `script_encoding`
- `script_length`
- `window_mode`
- `uac`
- `inspect`

`BuildResult.stub_path` and `BuildResult.output_exe` remain as compatibility
aliases for `template_executable_path` and `output_exe_path` respectively.

For Python builds, `template_executable_path` is a logical embedded-host name
rather than a real on-disk template path.

## Errors

Failures raise typed exceptions:

- `Bat2PeError`
- `BuildError`

Each exception exposes:

- `code`
- `path`
- `details`

Named error code constants are available for programmatic matching:

```python
from bat2pe import (
    ERR_INVALID_INPUT,         # 100
    ERR_UNSUPPORTED_INPUT,     # 101
    ERR_UNSUPPORTED_ENCODING,  # 102
    ERR_RESOURCE_NOT_FOUND,    # 103
    ERR_INVALID_EXECUTABLE,    # 104
    ERR_DIRECTORY_NOT_WRITABLE,# 105
    ERR_IO,                    # 106
    ERR_CLI_USAGE,             # 107
    ERR_VERIFY_MISMATCH,       # 108
    ERR_VERIFY_UAC_INTERACTIVE,# 109
)
```

Example:

```python
from bat2pe import BuildError, ERR_UNSUPPORTED_INPUT, build

try:
    build(input_bat_path="bad.txt", output_exe_path="bad.exe")
except BuildError as exc:
    if exc.code == ERR_UNSUPPORTED_INPUT:
        print("Not a .bat or .cmd file")
    print(exc.code)
    print(exc.path)
    print(exc.details)
```

## UAC Builds

Pass `uac=True` when you want the generated executable to request administrator
privileges through a Windows execution manifest.

```python
from bat2pe import build

result = build(
    input_bat_path="admin_task.bat",
    output_exe_path="admin_task.exe",
    uac=True,
)
```

Notes:

- `uac=False` is the default
- `uac=True` writes a `requireAdministrator` manifest into the generated exe
- child `cmd.exe` executions inherit elevation once the generated exe is
  running elevated
