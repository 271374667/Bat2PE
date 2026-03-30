use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

use crate::error::{
    Bat2PeError, ERR_INVALID_INPUT, ERR_RESOURCE_NOT_FOUND, ERR_UNSUPPORTED_ENCODING,
    ERR_UNSUPPORTED_INPUT, Result,
};
use crate::inspect::inspect_executable;
use crate::model::{
    BuildRequest, BuildResult, EmbeddedMetadata, IconInfo, RuntimeConfig, ScriptEncoding, StubPaths,
};
use crate::overlay::append_overlay;
use crate::resources::{apply_execution_level_manifest, apply_icon_resource};

const OVERLAY_SCHEMA_VERSION: u32 = 1;

pub fn locate_stub_binaries(executable: &Path) -> Result<StubPaths> {
    let directory = executable.parent().ok_or_else(|| {
        Bat2PeError::new(ERR_RESOURCE_NOT_FOUND, "failed to locate CLI directory")
            .with_path(executable.to_path_buf())
    })?;

    let console = directory.join("bat2pe-stub-console.exe");
    let windows = directory.join("bat2pe-stub-windows.exe");

    if !console.exists() {
        return Err(
            Bat2PeError::new(ERR_RESOURCE_NOT_FOUND, "missing console runtime stub")
                .with_path(console),
        );
    }

    if !windows.exists() {
        return Err(
            Bat2PeError::new(ERR_RESOURCE_NOT_FOUND, "missing hidden-window runtime stub")
                .with_path(windows),
        );
    }

    Ok(StubPaths { console, windows })
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

    let stub_path = request.stub_paths.for_window_mode(request.window_mode);
    if !stub_path.exists() {
        return Err(Bat2PeError::new(
            ERR_RESOURCE_NOT_FOUND,
            "runtime stub executable was not found",
        )
        .with_path(stub_path));
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
        .transpose()?;

    fs::copy(&stub_path, &output_exe_path)
        .map_err(|error| Bat2PeError::io(&output_exe_path, &error))?;

    if let Some(icon_path) = request.icon_path.as_deref() {
        apply_icon_resource(&output_exe_path, icon_path)?;
    }
    apply_execution_level_manifest(&output_exe_path, request.uac)?;

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
        icon,
        version_info: request.version_info.clone(),
    };

    let mut output = OpenOptions::new()
        .append(true)
        .open(&output_exe_path)
        .map_err(|error| Bat2PeError::io(&output_exe_path, &error))?;
    append_overlay(&mut output, &metadata, &script_bytes)?;
    output
        .flush()
        .map_err(|error| Bat2PeError::io(&output_exe_path, &error))?;

    let inspect = inspect_executable(&output_exe_path)?;
    Ok(BuildResult {
        output_exe_path,
        stub_path,
        script_encoding,
        script_length: script_bytes.len() as u64,
        window_mode: request.window_mode,
        uac: request.uac,
        inspect,
    })
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
}
