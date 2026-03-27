use std::path::Path;

use crate::error::Result;
use crate::model::InspectResult;
use crate::overlay::read_overlay_from_path;

pub fn inspect_executable(path: &Path) -> Result<InspectResult> {
    let overlay = read_overlay_from_path(path)?;
    Ok(InspectResult {
        exe_path: path.to_path_buf(),
        source_script_name: overlay.metadata.source_script_name,
        source_extension: overlay.metadata.source_extension,
        script_encoding: overlay.metadata.script_encoding,
        script_length: overlay.metadata.script_length,
        runtime: overlay.metadata.runtime,
        icon: overlay.metadata.icon,
        version_info: overlay.metadata.version_info,
        schema_version: overlay.metadata.schema_version,
    })
}
