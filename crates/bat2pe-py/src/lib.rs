#![allow(unsafe_op_in_unsafe_fn)]

use std::ffi::OsString;
use std::path::PathBuf;
use std::str::FromStr;

use bat2pe_core::{
    Bat2PeError, BuildRequest, ERR_RESOURCE_NOT_FOUND, VerifyRequest, VersionInfo, VersionTriplet,
    WindowMode, build_executable, inspect_executable, locate_template_executable, verify,
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
    input_bat_path,
    output_exe_path = None,
    *,
    visible = true,
    uac = false,
    icon_path = None,
    company_name = None,
    product_name = None,
    description = None,
    file_version = None,
    product_version = None,
    original_filename = None,
    internal_name = None,
    template_executable_path = None
))]
fn build(
    input_bat_path: String,
    output_exe_path: Option<String>,
    visible: bool,
    uac: bool,
    icon_path: Option<String>,
    company_name: Option<String>,
    product_name: Option<String>,
    description: Option<String>,
    file_version: Option<String>,
    product_version: Option<String>,
    original_filename: Option<String>,
    internal_name: Option<String>,
    template_executable_path: Option<String>,
) -> PyResult<String> {
    let mut version_info = VersionInfo::default();
    version_info.company_name = company_name;
    version_info.product_name = product_name;
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
        input_bat_path: PathBuf::from(input_bat_path),
        output_exe_path: output_exe_path.map(PathBuf::from),
        template_executable_path: resolve_template_executable_path(template_executable_path)
            .map_err(to_py_error)?,
        window_mode: if visible {
            WindowMode::Visible
        } else {
            WindowMode::Hidden
        },
        uac,
        icon_path: icon_path.map(PathBuf::from),
        version_info,
        overwrite: true,
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

fn resolve_template_executable_path(
    template_executable_path: Option<String>,
) -> Result<PathBuf, Bat2PeError> {
    let template_executable_path = template_executable_path
        .or_else(|| std::env::var("BAT2PE_TEMPLATE_EXE").ok())
        .or_else(|| std::env::var("BAT2PE_HOST_EXE").ok())
        .ok_or_else(|| {
            Bat2PeError::new(
                ERR_RESOURCE_NOT_FOUND,
                "missing bat2pe template executable; provide template_executable_path or BAT2PE_TEMPLATE_EXE",
            )
        })?;

    locate_template_executable(PathBuf::from(template_executable_path).as_path())
}

#[pymodule]
fn _native(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build, module)?)?;
    module.add_function(wrap_pyfunction!(inspect, module)?)?;
    module.add_function(wrap_pyfunction!(verify_pair, module)?)?;
    Ok(())
}
