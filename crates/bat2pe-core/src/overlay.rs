use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;

use crate::error::{Bat2PeError, ERR_INVALID_EXECUTABLE, Result};
use crate::model::EmbeddedMetadata;

const MAGIC: &[u8; 8] = b"B2PEPAY1";
const FOOTER_LEN: usize = 8 + 4 + 8 + 8;

#[derive(Debug, Clone)]
pub struct ParsedOverlay {
    pub metadata: EmbeddedMetadata,
    pub script_bytes: Vec<u8>,
}

pub fn append_overlay(
    writer: &mut File,
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

    writer
        .write_all(&metadata_bytes)
        .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
    writer
        .write_all(script_bytes)
        .map_err(|error| Bat2PeError::io(Path::new("<output>"), &error))?;
    writer
        .write_all(MAGIC)
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

pub fn read_overlay_from_path(path: &Path) -> Result<ParsedOverlay> {
    let mut bytes = Vec::new();
    let mut file = File::open(path).map_err(|error| Bat2PeError::io(path, &error))?;
    file.read_to_end(&mut bytes)
        .map_err(|error| Bat2PeError::io(path, &error))?;
    read_overlay_from_bytes(&bytes)
}

pub fn read_overlay_from_bytes(bytes: &[u8]) -> Result<ParsedOverlay> {
    if bytes.len() < FOOTER_LEN {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "file is too small to contain a bat2pe overlay",
        ));
    }

    let footer = &bytes[bytes.len() - FOOTER_LEN..];
    if &footer[..8] != MAGIC {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "missing bat2pe overlay footer",
        ));
    }

    let schema_version =
        u32::from_le_bytes(footer[8..12].try_into().expect("schema version footer"));
    let metadata_len =
        u64::from_le_bytes(footer[12..20].try_into().expect("metadata length footer")) as usize;
    let script_len =
        u64::from_le_bytes(footer[20..28].try_into().expect("script length footer")) as usize;

    let payload_len = metadata_len
        .checked_add(script_len)
        .ok_or_else(|| Bat2PeError::new(ERR_INVALID_EXECUTABLE, "overlay length overflow"))?;

    if payload_len + FOOTER_LEN > bytes.len() {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "overlay footer points outside the executable",
        ));
    }

    let payload_start = bytes.len() - FOOTER_LEN - payload_len;
    let metadata_start = payload_start;
    let metadata_end = metadata_start + metadata_len;
    let script_end = metadata_end + script_len;

    let mut metadata: EmbeddedMetadata =
        serde_json::from_slice(&bytes[metadata_start..metadata_end]).map_err(|error| {
            Bat2PeError::new(ERR_INVALID_EXECUTABLE, "failed to parse embedded metadata")
                .with_details(error.to_string())
        })?;
    metadata.schema_version = schema_version;

    Ok(ParsedOverlay {
        metadata,
        script_bytes: bytes[metadata_end..script_end].to_vec(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{RuntimeConfig, ScriptEncoding, VersionInfo, WindowMode};

    #[test]
    fn round_trip_overlay() {
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

        let mut file = File::create(&path).expect("create temp file");
        file.write_all(b"stub").expect("write stub");
        append_overlay(&mut file, &metadata, b"@echo off\r\n").expect("append overlay");
        drop(file);

        let parsed = read_overlay_from_path(&path).expect("read overlay");
        assert_eq!(parsed.metadata.source_extension, ".bat");
        assert_eq!(parsed.metadata.script_encoding, ScriptEncoding::Utf8);
        assert_eq!(parsed.script_bytes, b"@echo off\r\n");

        std::fs::remove_file(path).expect("remove temp file");
    }

    #[test]
    fn rejects_missing_magic() {
        let bytes = vec![0; FOOTER_LEN];
        let error = read_overlay_from_bytes(&bytes).expect_err("missing magic");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("overlay"));
    }

    #[test]
    fn rejects_footer_outside_file() {
        let mut bytes = vec![0; FOOTER_LEN];
        bytes[0..8].copy_from_slice(MAGIC);
        bytes[8..12].copy_from_slice(&1u32.to_le_bytes());
        bytes[12..20].copy_from_slice(&32u64.to_le_bytes());
        bytes[20..28].copy_from_slice(&16u64.to_le_bytes());

        let error = read_overlay_from_bytes(&bytes).expect_err("payload outside file");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("outside"));
    }

    #[test]
    fn rejects_invalid_metadata_json() {
        let metadata = b"not-json";
        let script = b"echo";
        let mut bytes = Vec::new();
        bytes.extend_from_slice(metadata);
        bytes.extend_from_slice(script);
        bytes.extend_from_slice(MAGIC);
        bytes.extend_from_slice(&1u32.to_le_bytes());
        bytes.extend_from_slice(&(metadata.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&(script.len() as u64).to_le_bytes());

        let error = read_overlay_from_bytes(&bytes).expect_err("invalid metadata");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
        assert!(error.message.contains("embedded metadata"));
    }
}
