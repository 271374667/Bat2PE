# Python API

`bat2pe` 同时提供面向对象和函数式两种 Python 入口。

## 架构

Python 包在执行 build、inspect、verify 操作时，**不会**调用 Rust CLI 子进程。

集成分为两部分：

- `bat2pe._native.pyd`
  Python 直接导入的 PyO3 原生扩展，内部嵌入了私有的 runtime-host 可执行文件字节码
- `bat2pe.exe`
  面向终端用户的独立 Rust CLI 程序

Python 调用原生扩展，原生扩展将内嵌的 runtime-host 可执行文件写入输出路径，根据窗口模式修补 PE 子系统字段，然后将 bat2pe 载荷资源写入该可执行文件。

这意味着 Python wheel 不再依赖外部的 `bat2pe.exe` 文件。CLI 和 Python 模块可以独立分发。

该扩展通过 PyO3 的 stable ABI 模式支持 CPython `3.10+`。

## 开发环境配置

将原生扩展构建到当前虚拟环境中：

```powershell
uv sync
uv run maturin develop
```

项目还提供了一个辅助脚本，用于构建 Rust 产物并将 Python 侧文件同步到 `python/bat2pe/`：

```powershell
uv run python scripts/compile.py
```

该脚本构建：

- `bat2pe.exe`
- `bat2pe_py.dll`

并同步以下 Python 包产物：

- `python/bat2pe/_native.pyd`

仅在你明确需要未优化的 Rust 构建时使用 `--debug`：

```powershell
uv run python scripts/compile.py --debug
```

`bat2pe-py` 通过自身的 Cargo `build.rs` 在 Rust 构建过程中编译内嵌的 runtime host。无需额外的 Python 侧模板文件。

## 导入

所有公开名称均可从顶层包导入：

```python
from bat2pe import (
    # 类
    Builder,
    Inspector,
    Verifier,
    # 函数式辅助方法
    build,
    inspect,
    verify,
    # 结果类型
    BuildResult,
    InspectResult,
    VerifyResult,
    # 嵌套模型类型
    IconInfo,
    RuntimeConfig,
    VerifyExecution,
    VersionInfo,
    VersionTriplet,
    # 异常
    Bat2PeError,
    BuildError,
    InspectError,
    VerifyError,
    # 错误码常量
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

## 面向对象用法

```python
from pathlib import Path

from bat2pe import Builder, Inspector, Verifier

builder = Builder(
    input_bat_path=Path("run.bat"),
    output_exe_path=Path("run.exe"),
    uac=True,
    company_name="Acme",
    product_name="Runner",
    description="批处理启动器",
    file_version="1.2.3",
    product_version="1.2.3",
)
build_result = builder.build()

inspector = Inspector(build_result.output_exe_path)
inspect_result = inspector.inspect()

verifier = Verifier(
    Path("run.bat"),
    build_result.output_exe_path,
    args=["alpha", "beta"],
)
verify_result = verifier.verify()
```

如果省略 `output_exe_path`，构建器会在输入脚本旁边生成同名 `.exe`。已存在的文件会被覆盖。

## 函数式用法

```python
from bat2pe import build, inspect, verify

build_result = build(
    input_bat_path="run.bat",
    output_exe_path="run.exe",
    visible=True,
    uac=False,
)

inspect_result = inspect("run.exe")
verify_result = verify("run.bat", "run.exe", args=["hello"])
```

## 返回值

Python 返回值均为标准库 dataclass：

- `BuildResult`
- `InspectResult`
- `VerifyResult`

嵌套的类型化对象包括：

- `VersionInfo`
- `VersionTriplet` — 支持 `str()` 输出 `"major.minor.patch"` 格式
- `RuntimeConfig`
- `IconInfo`
- `VerifyExecution`

`BuildResult` 暴露以下字段：

- `output_exe_path`
- `template_executable_path`
- `script_encoding`
- `script_length`
- `window_mode`
- `uac`
- `inspect`

`BuildResult.stub_path` 和 `BuildResult.output_exe` 作为兼容别名分别对应 `template_executable_path` 和 `output_exe_path`。

对于 Python 构建，`template_executable_path` 是一个逻辑上的内嵌主机名称，而非磁盘上的实际模板路径。

## 错误处理

失败时抛出类型化异常：

- `Bat2PeError`
- `BuildError`
- `InspectError`
- `VerifyError`

每个异常暴露以下属性：

- `code`
- `path`
- `details`

提供命名错误码常量用于程序化匹配：

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

示例：

```python
from bat2pe import BuildError, ERR_UNSUPPORTED_INPUT, build

try:
    build(input_bat_path="bad.txt", output_exe_path="bad.exe")
except BuildError as exc:
    if exc.code == ERR_UNSUPPORTED_INPUT:
        print("不是 .bat 或 .cmd 文件")
    print(exc.code)
    print(exc.path)
    print(exc.details)
```

## UAC 构建

当你希望生成的可执行文件通过 Windows 执行清单请求管理员权限时，传入 `uac=True`。

```python
from bat2pe import build

result = build(
    input_bat_path="admin_task.bat",
    output_exe_path="admin_task.exe",
    uac=True,
)
```

注意事项：

- `uac=False` 是默认值
- `uac=True` 会在生成的 exe 中写入 `requireAdministrator` 清单
- 子进程 `cmd.exe` 执行时会继承提升后的权限
- `verify()` 不支持 `uac=True` 的可执行文件，因为 UAC 提权是交互式的
