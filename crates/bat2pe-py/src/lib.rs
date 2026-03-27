#![allow(unsafe_op_in_unsafe_fn)]

use std::ffi::OsString;
use std::path::PathBuf;
use std::str::FromStr;

use bat2pe_core::{
    Bat2PeError, BuildRequest, ERR_RESOURCE_NOT_FOUND, StubPaths, VerifyRequest, VersionInfo,
    VersionTriplet, WindowMode, build_executable, inspect_executable, verify,
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyModule;

fn to_py_error(error: Bat2PeError) -> PyErr {
    PyRuntimeError::new_err(error.to_json_string())
}

fn serialize_json<T: serde::Serialize>(value: &T) -> PyResult<String> {
    serde_json::to_string(value).map_err(|error| {
        PyRuntimeError::new_err(format!("{{\"code\":500,\"message\":\"{error}\"}}"))
    })
}

#[pyfunction]
#[pyo3(signature = (
    input_script,
    output_exe = None,
    *,
    window = "visible",
    icon = None,
    company = None,
    product = None,
    description = None,
    file_version = None,
    product_version = None,
    original_filename = None,
    internal_name = None,
    stub_console = None,
    stub_windows = None
))]
fn build(
    input_script: String,
    output_exe: Option<String>,
    window: &str,
    icon: Option<String>,
    company: Option<String>,
    product: Option<String>,
    description: Option<String>,
    file_version: Option<String>,
    product_version: Option<String>,
    original_filename: Option<String>,
    internal_name: Option<String>,
    stub_console: Option<String>,
    stub_windows: Option<String>,
) -> PyResult<String> {
    let mut version_info = VersionInfo::default();
    version_info.company_name = company;
    version_info.product_name = product;
    version_info.file_description = description;
    version_info.original_filename = original_filename;
    version_info.internal_name = internal_name;
    version_info.file_version = file_version
        .map(|value| VersionTriplet::from_str(&value))
        .transpose()
        .map_err(to_py_error)?;
    version_info.product_version = product_version
        .map(|value| VersionTriplet::from_str(&value))
        .transpose()
        .map_err(to_py_error)?;

    let request = BuildRequest {
        input_script: PathBuf::from(input_script),
        output_exe: output_exe.map(PathBuf::from),
        window_mode: WindowMode::from_str(window).map_err(to_py_error)?,
        icon_path: icon.map(PathBuf::from),
        version_info,
        overwrite: true,
        stub_paths: resolve_stub_paths(stub_console, stub_windows).map_err(to_py_error)?,
    };

    let result = build_executable(&request).map_err(to_py_error)?;
    serialize_json(&result)
}

#[pyfunction]
fn inspect(executable: String) -> PyResult<String> {
    let result = inspect_executable(PathBuf::from(executable).as_path()).map_err(to_py_error)?;
    serialize_json(&result)
}

#[pyfunction]
#[pyo3(signature = (script_path, exe_path, args = None, cwd = None))]
fn verify_pair(
    script_path: String,
    exe_path: String,
    args: Option<Vec<String>>,
    cwd: Option<String>,
) -> PyResult<String> {
    let request = VerifyRequest {
        script_path: PathBuf::from(script_path),
        exe_path: PathBuf::from(exe_path),
        arguments: args
            .unwrap_or_default()
            .into_iter()
            .map(OsString::from)
            .collect(),
        working_dir: cwd.map(PathBuf::from),
    };
    let result = verify(&request).map_err(to_py_error)?;
    serialize_json(&result)
}

fn resolve_stub_paths(
    stub_console: Option<String>,
    stub_windows: Option<String>,
) -> Result<StubPaths, Bat2PeError> {
    let console = stub_console
        .or_else(|| std::env::var("BAT2PE_STUB_CONSOLE").ok())
        .ok_or_else(|| {
            Bat2PeError::new(
                ERR_RESOURCE_NOT_FOUND,
                "missing console stub path; build bat2pe-stub-console.exe with `cargo build -p bat2pe-stub-console -p bat2pe-stub-windows`, or pass stub_console / BAT2PE_STUB_CONSOLE",
            )
        })?;
    let windows = stub_windows
        .or_else(|| std::env::var("BAT2PE_STUB_WINDOWS").ok())
        .ok_or_else(|| {
            Bat2PeError::new(
                ERR_RESOURCE_NOT_FOUND,
                "missing hidden-window stub path; build bat2pe-stub-windows.exe with `cargo build -p bat2pe-stub-console -p bat2pe-stub-windows`, or pass stub_windows / BAT2PE_STUB_WINDOWS",
            )
        })?;

    Ok(StubPaths {
        console: PathBuf::from(console),
        windows: PathBuf::from(windows),
    })
}

#[pymodule]
fn _native(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build, module)?)?;
    module.add_function(wrap_pyfunction!(inspect, module)?)?;
    module.add_function(wrap_pyfunction!(verify_pair, module)?)?;
    Ok(())
}
