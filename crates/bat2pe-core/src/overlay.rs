use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::error::{Bat2PeError, ERR_INVALID_EXECUTABLE, Result};
use crate::model::EmbeddedMetadata;

const PAYLOAD_RESOURCE_TYPE: u16 = 10; // RT_RCDATA
const METADATA_RESOURCE_ID: u16 = 101;
const SCRIPT_RESOURCE_ID: u16 = 102;

const LEGACY_MAGIC: &[u8; 8] = b"B2PEPAY1";
const LEGACY_FOOTER_LEN: usize = 8 + 4 + 8 + 8;

#[derive(Debug, Clone)]
pub struct ParsedPayload {
    pub metadata: EmbeddedMetadata,
    pub script_bytes: Vec<u8>,
}

pub fn write_payload_resources(
    executable_path: &Path,
    metadata: &EmbeddedMetadata,
    script_bytes: &[u8],
) -> Result<()> {
    let metadata_bytes = serde_json::to_vec(metadata).map_err(|error| {
        Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "failed to serialize embedded metadata",
        )
        .with_details(error.to_string())
    })?;

    write_payload_resources_raw(executable_path, &metadata_bytes, script_bytes)
}

pub fn read_payload_from_path_if_present(path: &Path) -> Result<Option<ParsedPayload>> {
    match read_payload_resources_from_path(path) {
        Ok(payload) => Ok(Some(payload)),
        Err(resource_error) if is_missing_payload_resource_error(&resource_error) => {
            match read_legacy_overlay_from_path(path) {
                Ok(payload) => Ok(Some(payload)),
                Err(legacy_error) if is_missing_legacy_overlay_error(&legacy_error) => Ok(None),
                Err(legacy_error) => Err(legacy_error),
            }
        }
        Err(error) => Err(error),
    }
}

pub fn read_payload_from_path(path: &Path) -> Result<ParsedPayload> {
    read_payload_from_path_if_present(path)?.ok_or_else(|| {
        Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "missing bat2pe metadata payload resource",
        )
        .with_path(path.to_path_buf())
    })
}

fn is_missing_payload_resource_error(error: &Bat2PeError) -> bool {
    error.code == ERR_INVALID_EXECUTABLE && error.message.contains("payload resource")
}

fn is_missing_legacy_overlay_error(error: &Bat2PeError) -> bool {
    error.code == ERR_INVALID_EXECUTABLE
        && (error.message.contains("missing bat2pe legacy overlay")
            || error
                .message
                .contains("too small to contain a bat2pe legacy overlay"))
}

#[cfg(windows)]
fn write_payload_resources_raw(
    executable_path: &Path,
    metadata_bytes: &[u8],
    script_bytes: &[u8],
) -> Result<()> {
    use windows_sys::Win32::System::LibraryLoader::{BeginUpdateResourceW, EndUpdateResourceW};

    let executable_path_wide = to_wide(executable_path);
    let update_handle = unsafe { BeginUpdateResourceW(executable_path_wide.as_ptr(), 0) };
    if update_handle.is_null() {
        return Err(win32_error(
            executable_path,
            "failed to begin updating executable resources",
        ));
    }

    let update_result = (|| {
        update_payload_resource(
            update_handle,
            executable_path,
            METADATA_RESOURCE_ID,
            metadata_bytes,
            "metadata payload",
        )?;
        update_payload_resource(
            update_handle,
            executable_path,
            SCRIPT_RESOURCE_ID,
            script_bytes,
            "script payload",
        )?;
        Ok(())
    })();

    let discard_changes = i32::from(update_result.is_err());
    let finalized = unsafe { EndUpdateResourceW(update_handle, discard_changes) };
    if finalized == 0 {
        let finalize_error =
            win32_error(executable_path, "failed to finalize executable resources");
        if update_result.is_ok() {
            return Err(finalize_error);
        }
    }

    update_result
}

#[cfg(windows)]
fn update_payload_resource(
    update_handle: windows_sys::Win32::Foundation::HANDLE,
    executable_path: &Path,
    resource_id: u16,
    bytes: &[u8],
    label: &str,
) -> Result<()> {
    use windows_sys::Win32::System::LibraryLoader::UpdateResourceW;

    let size: u32 = bytes
        .len()
        .try_into()
        .map_err(|_| Bat2PeError::new(ERR_INVALID_EXECUTABLE, format!("{label} is too large")))?;

    let updated = unsafe {
        UpdateResourceW(
            update_handle,
            make_int_resource(PAYLOAD_RESOURCE_TYPE),
            make_int_resource(resource_id),
            0,
            bytes.as_ptr().cast_mut().cast(),
            size,
        )
    };
    if updated == 0 {
        return Err(win32_error(
            executable_path,
            format!("failed to write {label} resource"),
        ));
    }

    Ok(())
}

#[cfg(not(windows))]
fn write_payload_resources_raw(
    _executable_path: &Path,
    _metadata_bytes: &[u8],
    _script_bytes: &[u8],
) -> Result<()> {
    Err(Bat2PeError::new(
        ERR_INVALID_EXECUTABLE,
        "payload resources are only supported on Windows",
    ))
}

fn read_payload_resources_from_path(path: &Path) -> Result<ParsedPayload> {
    let metadata_bytes = read_payload_resource_from_path(path, METADATA_RESOURCE_ID, "metadata")?;
    let script_bytes = read_payload_resource_from_path(path, SCRIPT_RESOURCE_ID, "script")?;

    let metadata: EmbeddedMetadata = serde_json::from_slice(&metadata_bytes).map_err(|error| {
        Bat2PeError::new(ERR_INVALID_EXECUTABLE, "failed to parse embedded metadata")
            .with_path(path.to_path_buf())
            .with_details(error.to_string())
    })?;

    Ok(ParsedPayload {
        metadata,
        script_bytes,
    })
}

#[cfg(windows)]
fn read_payload_resource_from_path(path: &Path, resource_id: u16, label: &str) -> Result<Vec<u8>> {
    use windows_sys::Win32::Foundation::FreeLibrary;
    use windows_sys::Win32::System::LibraryLoader::{
        FindResourceW, LOAD_LIBRARY_AS_DATAFILE, LoadLibraryExW, LoadResource, LockResource,
        SizeofResource,
    };

    let module = unsafe {
        LoadLibraryExW(
            to_wide(path).as_ptr(),
            std::ptr::null_mut(),
            LOAD_LIBRARY_AS_DATAFILE,
        )
    };
    if module.is_null() {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "failed to load executable as a data file",
        )
        .with_path(path.to_path_buf()));
    }

    let result = (|| {
        let resource = unsafe {
            FindResourceW(
                module,
                make_int_resource(resource_id),
                make_int_resource(PAYLOAD_RESOURCE_TYPE),
            )
        };
        if resource.is_null() {
            return Err(Bat2PeError::new(
                ERR_INVALID_EXECUTABLE,
                format!("missing bat2pe {label} payload resource"),
            )
            .with_path(path.to_path_buf()));
        }

        let size = unsafe { SizeofResource(module, resource) };
        if size == 0 {
            return Err(Bat2PeError::new(
                ERR_INVALID_EXECUTABLE,
                format!("bat2pe {label} payload resource is empty"),
            )
            .with_path(path.to_path_buf()));
        }

        let loaded = unsafe { LoadResource(module, resource) };
        if loaded.is_null() {
            return Err(win32_error(
                path,
                format!("failed to load {label} payload resource"),
            ));
        }

        let pointer = unsafe { LockResource(loaded) };
        if pointer.is_null() {
            return Err(win32_error(
                path,
                format!("failed to lock {label} payload resource"),
            ));
        }

        Ok(unsafe { std::slice::from_raw_parts(pointer.cast(), size as usize) }.to_vec())
    })();

    unsafe {
        FreeLibrary(module);
    }

    result
}

#[cfg(not(windows))]
fn read_payload_resource_from_path(
    _path: &Path,
    _resource_id: u16,
    _label: &str,
) -> Result<Vec<u8>> {
    Err(Bat2PeError::new(
        ERR_INVALID_EXECUTABLE,
        "payload resources are only supported on Windows",
    ))
}

#[cfg(windows)]
fn make_int_resource(value: u16) -> *const u16 {
    value as usize as *const u16
}

#[cfg(windows)]
fn to_wide(value: &Path) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;

    value
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

#[cfg(windows)]
fn win32_error(path: &Path, message: impl Into<String>) -> Bat2PeError {
    let error = std::io::Error::last_os_error();
    Bat2PeError::new(ERR_INVALID_EXECUTABLE, message)
        .with_path(path.to_path_buf())
        .with_details(error.to_string())
}

fn read_legacy_overlay_from_path(path: &Path) -> Result<ParsedPayload> {
    let mut bytes = Vec::new();
    let mut file = File::open(path).map_err(|error| Bat2PeError::io(path, &error))?;
    file.read_to_end(&mut bytes)
        .map_err(|error| Bat2PeError::io(path, &error))?;
    read_legacy_overlay_from_bytes(&bytes)
}

fn read_legacy_overlay_from_bytes(bytes: &[u8]) -> Result<ParsedPayload> {
    if bytes.len() < LEGACY_FOOTER_LEN {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "file is too small to contain a bat2pe legacy overlay",
        ));
    }

    let footer = &bytes[bytes.len() - LEGACY_FOOTER_LEN..];
    if &footer[..8] != LEGACY_MAGIC {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "missing bat2pe legacy overlay footer",
        ));
    }

    let schema_version =
        u32::from_le_bytes(footer[8..12].try_into().expect("schema version footer"));
    let metadata_len =
        u64::from_le_bytes(footer[12..20].try_into().expect("metadata length footer")) as usize;
    let script_len =
        u64::from_le_bytes(footer[20..28].try_into().expect("script length footer")) as usize;

    let payload_len = metadata_len.checked_add(script_len).ok_or_else(|| {
        Bat2PeError::new(ERR_INVALID_EXECUTABLE, "legacy overlay length overflow")
    })?;

    if payload_len + LEGACY_FOOTER_LEN > bytes.len() {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "legacy overlay footer points outside the executable",
        ));
    }

    let payload_start = bytes.len() - LEGACY_FOOTER_LEN - payload_len;
    let metadata_start = payload_start;
    let metadata_end = metadata_start + metadata_len;
    let script_end = metadata_end + script_len;

    let mut metadata: EmbeddedMetadata =
        serde_json::from_slice(&bytes[metadata_start..metadata_end]).map_err(|error| {
            Bat2PeError::new(ERR_INVALID_EXECUTABLE, "failed to parse embedded metadata")
                .with_details(error.to_string())
        })?;
    metadata.schema_version = schema_version;

    Ok(ParsedPayload {
        metadata,
        script_bytes: bytes[metadata_end..script_end].to_vec(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{RuntimeConfig, ScriptEncoding, VersionInfo, WindowMode};

    fn append_legacy_overlay(
        writer: &mut std::fs::File,
        metadata: &EmbeddedMetadata,
        script_bytes: &[u8],
    ) -> Result<()> {
        use std::io::Write;

        let metadata_bytes = serde_json::to_vec(metadata).map_err(|error| {
            Bat2PeError::new(
                ERR_INVALID_EXECUTABLE,
                "failed to serialize embedded metadata",
            )
            .with_details(error.to_string())
        })?;

        writer
            .write_all(&metadata_bytes)
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        writer
            .write_all(script_bytes)
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        writer
            .write_all(LEGACY_MAGIC)
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        writer
            .write_all(&metadata.schema_version.to_le_bytes())
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        writer
            .write_all(&(metadata_bytes.len() as u64).to_le_bytes())
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        writer
            .write_all(&(script_bytes.len() as u64).to_le_bytes())
            .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
        Ok(())
    }

    #[test]
    fn legacy_overlay_round_trip() {
        let metadata = EmbeddedMetadata {
            schema_version: 1,
            source_script_name: "demo.bat".to_string(),
            source_extension: ".bat".to_string(),
            script_encoding: ScriptEncoding::Utf8,
            script_length: 12,
            runtime: RuntimeConfig {
                window_mode: WindowMode::Hidden,
                temp_script_suffix: ".cmd".to_string(),
                strict_dp0: true,
                uac: false,
            },
            icon: None,
            version_info: VersionInfo::default(),
        };

        let mut path = std::env::temp_dir();
        path.push(format!("bat2pe-overlay-{}.bin", std::process::id()));

        let mut file = std::fs::File::create(&path).expect("create temp file");
        use std::io::Write;
        file.write_all(b"stub").expect("write stub");
        append_legacy_overlay(&mut file, &metadata, b"@echo off\r\n").expect("append overlay");
        drop(file);

        let parsed = read_legacy_overlay_from_path(&path).expect("read legacy overlay");
        assert_eq!(parsed.metadata.source_extension, ".bat");
        assert_eq!(parsed.metadata.script_encoding, ScriptEncoding::Utf8);
        assert_eq!(parsed.script_bytes, b"@echo off\r\n");

        std::fs::remove_file(path).expect("remove temp file");
    }

    #[test]
    fn rejects_missing_legacy_magic() {
        let bytes = vec![0; LEGACY_FOOTER_LEN];
        let error = read_legacy_overlay_from_bytes(&bytes).expect_err("missing magic");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("overlay"));
    }

    #[test]
    fn rejects_legacy_footer_outside_file() {
        let mut bytes = vec![0; LEGACY_FOOTER_LEN];
        bytes[0..8].copy_from_slice(LEGACY_MAGIC);
        bytes[8..12].copy_from_slice(&1u32.to_le_bytes());
        bytes[12..20].copy_from_slice(&32u64.to_le_bytes());
        bytes[20..28].copy_from_slice(&16u64.to_le_bytes());

        let error = read_legacy_overlay_from_bytes(&bytes).expect_err("payload outside file");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("outside"));
    }

    #[test]
    fn rejects_invalid_legacy_metadata_json() {
        let metadata = b"not-json";
        let script = b"echo";
        let mut bytes = Vec::new();
        bytes.extend_from_slice(metadata);
        bytes.extend_from_slice(script);
        bytes.extend_from_slice(LEGACY_MAGIC);
        bytes.extend_from_slice(&1u32.to_le_bytes());
        bytes.extend_from_slice(&(metadata.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&(script.len() as u64).to_le_bytes());

        let error = read_legacy_overlay_from_bytes(&bytes).expect_err("invalid metadata");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("embedded metadata"));
    }

    #[cfg(windows)]
    #[test]
    fn read_payload_from_path_if_present_returns_none_when_payload_is_missing() {
        let path = std::env::current_exe().expect("current exe");
        let parsed = read_payload_from_path_if_present(&path).expect("optional payload read");
        assert!(parsed.is_none());
    }
}
