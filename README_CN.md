# Bat2PE

<div align="center">

![Bat2PE](https://socialify.git.ci/271374667/Bat2PE/image?description=1&language=1&name=1&owner=1&pattern=Plus&theme=Auto)

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#快速上手)
[![Rust](https://img.shields.io/badge/rust-1.85%2B-orange.svg)](#快速上手)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-success.svg)](#你会在意的亮点)
[![License](https://img.shields.io/badge/license-MPL--2.0-green.svg)](LICENSE)

[English README](README.md) | [中文说明](README_CN.md) | [Python API](docs/python-api-cn.md) 

**通过CLI或者封装好的Python模块将 `.bat` / `.cmd` 脚本转为可执行的 `.exe`**  
**和原脚本运行逻辑完全兼容，可加图标，自定义exe文件信息，UAC提权，可通过卡巴斯基和常规杀毒软件，生成的exe独立运行无额外依赖**

</div>

> 现在市面上有类似Bat To Exe Converter这样的 bat 转 exe 的解决方案，但是对于想将转换步骤融入自动化工作流的专业人士始终没有较好的解决方案，Bat2PE 不提供可视化界面，而是提供一个 CLI 还有可直接调用的 Python 模块，能够通过命令快速将 bat 脚本转换成 exe 可执行文件，将分发的最后一步融入您的工作流

使用Bat2PE转换的exe运行逻辑和原 bat 脚本完全一致，支持 `%~dp0` 等特殊变量，可加软件图标，自定义版本号、公司名称等文件信息，一行代码即可添加UAC提权逻辑，可通过卡巴斯基(免费版)和常规杀毒软件，生成的exe独立运行无额外依赖

## 你会在意的亮点

- **`build → inspect → verify` 一条龙**：生成、检查、验证不需要换工具
- **`%~dp0` 语义保留**：临时脚本写在 `.exe` 旁边而非 `%TEMP%`，最大限度减少相对路径失效
- **窗口模式**：默认 `hidden`（静默后台），加 `--visible` 切换为前台控制台
- **多编码支持**：`UTF-8` · `UTF-8 BOM` · `UTF-16 LE BOM` · `ANSI/GBK` 自动识别
- **双入口**：CLI 用于终端和 CI/CD，Python API 用于脚本集成与自动化工作流
- **Windows 原生资源写入**：图标、版本号、公司名称等元数据写入 PE 资源段，资源管理器直接可见
- **UAC 提权**：一个 `--uac` 或 `uac=True` 即可让生成的 `.exe` 请求管理员权限
- **杀毒友好**：生成的 exe 可通过卡巴斯基免费版和常规杀毒软件扫描
- **零额外依赖**：生成的 `.exe` 独立运行，无需安装运行时

## 安装

### Python 用户

```bash
pip install bat2pe
```

安装后即可在 Python 代码中 `from bat2pe import build` 直接调用。

### CLI 用户

从 [GitHub Releases](https://github.com/271374667/Bat2PE/releases) 下载 `bat2pe.exe` 即可使用。

### 系统要求

| 依赖 | 最低版本 |
|------|---------|
| 操作系统 | Windows 10 / 11（x64） |
| Python（仅 Python API 需要） | 3.10+ |
| Rust（仅从源码构建需要） | 1.85+ |

## 快速上手

### CLI 用法

**最简单的转换**——一行命令，在脚本旁生成同名 `.exe`：

```powershell
bat2pe build run.bat
```

**指定输出路径和图标**：

```powershell
bat2pe build run.bat --output-exe-path dist\run.exe --icon-path app.ico
```

**显示控制台窗口**（适合需要交互的前台工具）：

```powershell
bat2pe build run.bat --visible
```

**添加 UAC 管理员提权**：

```powershell
bat2pe build admin.cmd --uac
```

**写入完整的版本元数据**：

```powershell
bat2pe build run.bat ^
  --company "Acme Corp" ^
  --product "My Tool" ^
  --description "启动器" ^
  --file-version 1.2.3 ^
  --product-version 1.2.3
```

> 💡 除 `build` 外还有 `inspect`（查看 exe 元信息）和 `verify`（校验行为一致性）子命令，运行 `bat2pe <command> --help` 查看完整说明。

### Python 用法

#### 函数式 API（推荐快速使用）

```python
from bat2pe import build

result = build(input_bat_path="run.bat")
print(result.output_exe_path)  # run.exe
```

#### 面向对象 API（推荐复杂场景）

```python
from bat2pe import Builder

builder = Builder(
    input_bat_path="run.bat",
    output_exe_path="dist/run.exe",
    # visible defaults to False (hidden); set True for console window
    icon_path="app.ico",
    company_name="Acme Corp",
    product_name="My Tool",
    file_version="1.2.3",
)
result = builder.build()
```

#### UAC 提权

```python
from bat2pe import build

result = build(
    input_bat_path="admin_task.bat",
    uac=True,
)
```

#### 错误处理

```python
from bat2pe import build, BuildError, ERR_UNSUPPORTED_INPUT

try:
    build(input_bat_path="readme.txt")
except BuildError as e:
    if e.code == ERR_UNSUPPORTED_INPUT:
        print("仅支持 .bat 或 .cmd 文件")
    print(e.code, e.path, e.details)
```

更完整的 Python API 参考（含 `inspect`、`verify` 等高级用法）见 [docs/python-api-cn.md](docs/python-api-cn.md)。

## 工作原理

Bat2PE 将批处理脚本嵌入一个原生 Windows PE 可执行文件中，生成的 `.exe` 运行时会：

1. 从自身 PE 资源中读取嵌入的脚本内容
2. 在 `.exe` 所在目录写入一个临时 `.cmd` 文件（带隐藏属性）
3. 通过 `cmd.exe /d /c` 执行该临时脚本，完整转发命令行参数
4. 执行完毕后自动删除临时文件，并处理异常中断的孤儿清理

**为什么不写到 `%TEMP%`？** 因为很多批处理脚本依赖 `%~dp0` 来定位旁边的配置文件或资源目录。将临时脚本放在 `.exe` 旁边可以最大限度保留这个语义，使转换后的行为与原脚本一致。

### 架构概览

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│   CLI 用户   │    │  Python 用户 │    │    CI/CD 管道    │
└──────┬───────┘    └──────┬───────┘    └────────┬─────────┘
       │                   │                     │
       ▼                   ▼                     ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│  bat2pe.exe  │    │ bat2pe._native│   │  bat2pe (Python)  │
│  (Rust CLI)  │    │  (PyO3 扩展) │    │   pip install     │
└──────┬───────┘    └──────┬───────┘    └────────┬─────────┘
       │                   │                     │
       └───────────────────┴─────────────────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │   bat2pe-core   │
                  │ (Rust 核心库)   │
                  └─────────────────┘
```


## 项目构建（开发者）

如果你想从源码构建或参与开发：

### 环境准备

```powershell
# 安装依赖
uv sync
```

### 构建所有产物

```powershell
# 推荐方式（一键构建 CLI + Python 扩展）
uv run python scripts/compile.py

# 如需 debug 构建
uv run python scripts/compile.py --debug
```

构建完成后会产出：

| 产物 | 说明 |
|------|------|
| `target/release/bat2pe.exe` | 独立 CLI 程序 |
| `python/bat2pe/_native.pyd` | Python 原生扩展模块 |

### 手动构建

```powershell
cargo build --release -p bat2pe -p bat2pe-py
uv run maturin develop
```

### 运行测试

```powershell
# Rust 单元测试
cargo test --workspace

# Python 集成测试（含 CLI、Python API、运行时行为等）
uv run pytest
```

测试覆盖范围包括：

- CLI 帮助输出、参数校验、build/inspect/verify 全流程
- Python API 函数式 + 面向对象双接口
- 编码识别与不支持编码的拒绝
- 类型化异常映射
- UAC 清单写入
- Windows 原生版本资源写入
- 运行时 `%~dp0` 行为与工作目录保留
- 隐藏窗口退出码
- 临时文件清理与强制终止清理
- Python 3.10 语法兼容性检查


## 当前阶段与已知限制

Bat2PE 已经适合**单脚本、启动器风格的批处理项目**。`build → inspect → verify` 工作流完整可用。


| 项目 | 状态 |
|------|------|
| 单 `.bat` / `.cmd` 脚本转换 | ✅ 完整支持 |
| 图标与版本元数据 | ✅ 写入 Windows 原生 PE 资源 |
| UAC 管理员提权 | ✅ 支持 |
| 隐藏窗口模式 | ✅ 支持 |
| 多编码自动识别 | ✅ UTF-8 / UTF-8 BOM / UTF-16 LE BOM / ANSI/GBK |


## 许可证

本项目基于 [MPL-2.0](LICENSE) 许可证开源。
