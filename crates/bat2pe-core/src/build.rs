use std::ffi::OsStr;
use std::fs;
use std::path::Path;

use crate::error::{
    Bat2PeError, ERR_INVALID_INPUT, ERR_RESOURCE_NOT_FOUND, ERR_UNSUPPORTED_ENCODING,
    ERR_UNSUPPORTED_INPUT, Result,
};
use crate::inspect::inspect_executable;
use crate::model::{
    BuildRequest, BuildResult, EmbeddedMetadata, IconInfo, RuntimeConfig, ScriptEncoding,
    TemplateExecutable,
};
use crate::overlay::write_payload_resources;
use crate::resources::{
    DEFAULT_ICON_FILE_NAME, DEFAULT_ICON_SOURCE_PATH, apply_default_icon_resource,
    apply_executable_subsystem, apply_execution_level_manifest, apply_icon_resource,
    apply_version_resource, default_icon_size,
};

const OVERLAY_SCHEMA_VERSION: u32 = 1;

pub fn locate_template_executable(executable: &Path) -> Result<std::path::PathBuf> {
    if executable.exists() {
        return Ok(executable.to_path_buf());
    }

    Err(Bat2PeError::new(
        ERR_RESOURCE_NOT_FOUND,
        "bat2pe template executable was not found",
    )
    .with_path(executable.to_path_buf()))
}

pub fn detect_script_encoding(bytes: &[u8]) -> Result<ScriptEncoding> {
    if bytes.starts_with(&[0xEF, 0xBB, 0xBF]) {
        return Ok(ScriptEncoding::Utf8Bom);
    }
    if bytes.starts_with(&[0xFF, 0xFE]) {
        return Ok(ScriptEncoding::Utf16LeBom);
    }

    let zero_count = bytes.iter().filter(|byte| **byte == 0).count();
    if zero_count > 0 {
        return Err(Bat2PeError::new(
            ERR_UNSUPPORTED_ENCODING,
            "unsupported script encoding; only UTF-8, UTF-8 BOM, UTF-16 LE BOM, and ANSI/GBK are accepted",
        ));
    }

    if std::str::from_utf8(bytes).is_ok() {
        return Ok(ScriptEncoding::Utf8);
    }

    Ok(ScriptEncoding::AnsiGbk)
}

pub fn read_script_bytes(path: &Path) -> Result<(Vec<u8>, ScriptEncoding)> {
    let extension = normalized_script_extension(path)?;
    let bytes = fs::read(path).map_err(|error| Bat2PeError::io(path, &error))?;
    if bytes.is_empty() {
        return Err(Bat2PeError::new(ERR_INVALID_INPUT, "input script is empty").with_path(path));
    }

    let encoding = detect_script_encoding(&bytes)?;

    if extension != ".bat" && extension != ".cmd" {
        return Err(
            Bat2PeError::new(ERR_UNSUPPORTED_INPUT, "input must be a .bat or .cmd file")
                .with_path(path),
        );
    }

    Ok((bytes, encoding))
}

pub fn derive_output_exe_path(input_bat_path: &Path) -> Result<std::path::PathBuf> {
    let extension = normalized_script_extension(input_bat_path)?;
    if extension != ".bat" && extension != ".cmd" {
        return Err(
            Bat2PeError::new(ERR_UNSUPPORTED_INPUT, "input must be a .bat or .cmd file")
                .with_path(input_bat_path.to_path_buf()),
        );
    }

    Ok(input_bat_path.with_extension("exe"))
}

pub fn build_executable(request: &BuildRequest) -> Result<BuildResult> {
    let (script_bytes, script_encoding) = read_script_bytes(&request.input_bat_path)?;
    let source_extension = normalized_script_extension(&request.input_bat_path)?;
    let output_exe_path = request
        .output_exe_path
        .clone()
        .unwrap_or(derive_output_exe_path(&request.input_bat_path)?);
    let version_info = resolved_version_info(&request.version_info, &output_exe_path);

    if let TemplateExecutable::Path(path) = &request.template_executable {
        if !path.exists() {
            return Err(Bat2PeError::new(
                ERR_RESOURCE_NOT_FOUND,
                "bat2pe template executable was not found",
            )
            .with_path(path.clone()));
        }
    }

    if !request.overwrite && output_exe_path.exists() {
        return Err(Bat2PeError::new(
            ERR_INVALID_INPUT,
            "output executable already exists and overwrite is disabled",
        )
        .with_path(output_exe_path.clone()));
    }

    let icon = request
        .icon_path
        .as_deref()
        .map(load_icon_info)
        .transpose()?
        .unwrap_or_else(default_icon_info);

    write_template_executable(&request.template_executable, &output_exe_path)?;

    apply_executable_subsystem(&output_exe_path, request.window_mode)?;
    apply_execution_level_manifest(&output_exe_path, request.uac)?;
    apply_version_resource(&output_exe_path, &version_info)?;
    if let Some(icon_path) = request.icon_path.as_deref() {
        apply_icon_resource(&output_exe_path, icon_path)?;
    } else {
        apply_default_icon_resource(&output_exe_path)?;
    }

    let metadata = EmbeddedMetadata {
        schema_version: OVERLAY_SCHEMA_VERSION,
        source_script_name: request
            .input_bat_path
            .file_name()
            .map(|value| value.to_string_lossy().to_string())
            .unwrap_or_else(|| "script.cmd".to_string()),
        source_extension,
        script_encoding,
        script_length: script_bytes.len() as u64,
        runtime: RuntimeConfig {
            window_mode: request.window_mode,
            temp_script_suffix: ".cmd".to_string(),
            strict_dp0: true,
            uac: request.uac,
        },
        icon: Some(icon),
        version_info: version_info.clone(),
    };

    write_payload_resources(&output_exe_path, &metadata, &script_bytes)?;

    let inspect = inspect_executable(&output_exe_path)?;
    Ok(BuildResult {
        output_exe_path,
        template_executable_path: request.template_executable.logical_path().to_path_buf(),
        script_encoding,
        script_length: script_bytes.len() as u64,
        window_mode: request.window_mode,
        uac: request.uac,
        inspect,
    })
}

fn default_icon_info() -> IconInfo {
    IconInfo {
        file_name: DEFAULT_ICON_FILE_NAME.to_string(),
        source_path: DEFAULT_ICON_SOURCE_PATH.to_string(),
        size: default_icon_size(),
    }
}

fn resolved_version_info(
    version_info: &crate::model::VersionInfo,
    output_exe_path: &Path,
) -> crate::model::VersionInfo {
    let mut resolved = version_info.clone();

    if resolved.original_filename.is_none() {
        resolved.original_filename = path_component_to_string(output_exe_path.file_name());
    }

    if resolved.internal_name.is_none() {
        resolved.internal_name = path_component_to_string(output_exe_path.file_stem());
    }

    resolved
}

fn path_component_to_string(component: Option<&OsStr>) -> Option<String> {
    let value = component?.to_string_lossy().to_string();
    if value.is_empty() { None } else { Some(value) }
}

fn write_template_executable(
    template_executable: &TemplateExecutable,
    output_exe_path: &Path,
) -> Result<()> {
    match template_executable {
        TemplateExecutable::Path(path) => {
            fs::copy(path, output_exe_path)
                .map_err(|error| Bat2PeError::io(output_exe_path, &error))?;
        }
        TemplateExecutable::Embedded { bytes, .. } => {
            fs::write(output_exe_path, bytes)
                .map_err(|error| Bat2PeError::io(output_exe_path, &error))?;
        }
    }

    Ok(())
}

fn normalized_script_extension(path: &Path) -> Result<String> {
    let extension = path
        .extension()
        .map(|value| format!(".{}", value.to_string_lossy().to_ascii_lowercase()))
        .ok_or_else(|| {
            Bat2PeError::new(ERR_UNSUPPORTED_INPUT, "input must end with .bat or .cmd")
                .with_path(path.to_path_buf())
        })?;

    if extension == ".bat" || extension == ".cmd" {
        Ok(extension)
    } else {
        Err(
            Bat2PeError::new(ERR_UNSUPPORTED_INPUT, "input must end with .bat or .cmd")
                .with_path(path.to_path_buf()),
        )
    }
}

fn load_icon_info(path: &Path) -> Result<IconInfo> {
    let extension = path
        .extension()
        .map(|value| value.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_default();
    if extension != "ico" {
        return Err(
            Bat2PeError::new(ERR_UNSUPPORTED_INPUT, "only .ico icon files are supported")
                .with_path(path.to_path_buf()),
        );
    }

    let metadata = fs::metadata(path).map_err(|error| Bat2PeError::io(path, &error))?;
    Ok(IconInfo {
        file_name: path
            .file_name()
            .map(|value| value.to_string_lossy().to_string())
            .unwrap_or_else(|| "icon.ico".to_string()),
        source_path: path.to_string_lossy().to_string(),
        size: metadata.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::VersionInfo;

    #[test]
    fn detects_utf8_bom() {
        let bytes = [0xEF, 0xBB, 0xBF, b'@', b'e', b'c', b'h', b'o'];
        assert_eq!(
            detect_script_encoding(&bytes).expect("encoding"),
            ScriptEncoding::Utf8Bom
        );
    }

    #[test]
    fn detects_gbk_fallback() {
        let bytes = [0xC4, 0xE3, 0xBA, 0xC3];
        assert_eq!(
            detect_script_encoding(&bytes).expect("encoding"),
            ScriptEncoding::AnsiGbk
        );
    }

    #[test]
    fn rejects_bomless_zero_bytes() {
        let bytes = [b'@', 0x00, b'e', 0x00, b'c', 0x00, b'h', 0x00, b'o', 0x00];
        let error = detect_script_encoding(&bytes).expect_err("unsupported encoding");
        assert_eq!(error.code, ERR_UNSUPPORTED_ENCODING);
    }

    #[test]
    fn derives_default_output_path() {
        let output = derive_output_exe_path(Path::new(r"G:\demo\run.cmd")).expect("output path");
        assert_eq!(output, std::path::PathBuf::from(r"G:\demo\run.exe"));
    }

    #[test]
    fn fills_version_names_from_output_path_when_missing() {
        let version_info = resolved_version_info(
            &VersionInfo::default(),
            Path::new(r"G:\demo\release\launcher.exe"),
        );

        assert_eq!(
            version_info.original_filename.as_deref(),
            Some("launcher.exe")
        );
        assert_eq!(version_info.internal_name.as_deref(), Some("launcher"));
    }

    #[test]
    fn preserves_explicit_version_name_overrides() {
        let version_info = resolved_version_info(
            &VersionInfo {
                original_filename: Some("custom.exe".to_string()),
                internal_name: Some("custom-name".to_string()),
                ..VersionInfo::default()
            },
            Path::new(r"G:\demo\release\launcher.exe"),
        );

        assert_eq!(
            version_info.original_filename.as_deref(),
            Some("custom.exe")
        );
        assert_eq!(version_info.internal_name.as_deref(), Some("custom-name"));
    }
}
