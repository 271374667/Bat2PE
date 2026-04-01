#![allow(unsafe_op_in_unsafe_fn)]

use std::path::PathBuf;
use std::str::FromStr;

use bat2pe_core::{
    Bat2PeError, BuildRequest, TemplateExecutable, VersionInfo, VersionTriplet, WindowMode,
    build_executable,
};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyModule;

const EMBEDDED_RUNTIME_HOST_BYTES: &[u8] =
    include_bytes!(env!("BAT2PE_EMBEDDED_RUNTIME_HOST_PATH"));
const EMBEDDED_RUNTIME_HOST_LABEL: &str = env!("BAT2PE_EMBEDDED_RUNTIME_HOST_LABEL");

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
    visible = false,
    uac = false,
    icon_path = None,
    company_name = None,
    product_name = None,
    description = None,
    file_version = None,
    product_version = None,
    original_filename = None,
    internal_name = None
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
        template_executable: TemplateExecutable::Embedded {
            logical_path: PathBuf::from(EMBEDDED_RUNTIME_HOST_LABEL),
            bytes: EMBEDDED_RUNTIME_HOST_BYTES,
        },
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

#[pymodule]
fn _native(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build, module)?)?;
    Ok(())
}
