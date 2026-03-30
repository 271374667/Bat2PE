use std::fs;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use crate::error::{Bat2PeError, ERR_INVALID_EXECUTABLE, ERR_INVALID_INPUT, ERR_IO, Result};
use crate::model::{VersionInfo, VersionTriplet, WindowMode};

const RT_ICON: u16 = 3;
const RT_GROUP_ICON: u16 = 14;
const RT_VERSION: u16 = 16;
const RT_MANIFEST: u16 = 24;
const GROUP_ICON_RESOURCE_ID: u16 = 1;
const MANIFEST_RESOURCE_ID: u16 = 1;
const VERSION_RESOURCE_ID: u16 = 1;
const VERSION_RESOURCE_LANGUAGE: u16 = 0x0409;
const VERSION_STRING_LANGUAGE_CODEPAGE: &str = "040904B0";
const VS_FFI_SIGNATURE: u32 = 0xFEEF04BD;
const VS_FFI_STRUC_VERSION: u32 = 0x0001_0000;
const VOS_NT_WINDOWS32: u32 = 0x0004_0004;
const VFT_APP: u32 = 0x0000_0001;
const VS_FFI_FILEFLAGSMASK: u32 = 0x0000_003F;
const DOS_SIGNATURE: u16 = 0x5A4D;
const PE_SIGNATURE: u32 = 0x0000_4550;
const PE_SUBSYSTEM_OFFSET: u64 = 68;
const IMAGE_SUBSYSTEM_WINDOWS_GUI: u16 = 2;
const IMAGE_SUBSYSTEM_WINDOWS_CUI: u16 = 3;
const AS_INVOKER_MANIFEST: &str = concat!(
    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
    "<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">",
    "<trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">",
    "<security><requestedPrivileges>",
    "<requestedExecutionLevel level=\"asInvoker\" uiAccess=\"false\"/>",
    "</requestedPrivileges></security>",
    "</trustInfo></assembly>",
);
const REQUIRE_ADMINISTRATOR_MANIFEST: &str = concat!(
    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
    "<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">",
    "<trustInfo xmlns=\"urn:schemas-microsoft-com:asm.v3\">",
    "<security><requestedPrivileges>",
    "<requestedExecutionLevel level=\"requireAdministrator\" uiAccess=\"false\"/>",
    "</requestedPrivileges></security>",
    "</trustInfo></assembly>",
);

#[derive(Debug)]
struct ParsedIcoImage {
    width: u8,
    height: u8,
    color_count: u8,
    reserved: u8,
    planes: u16,
    bit_count: u16,
    bytes: Vec<u8>,
}

#[derive(Debug)]
struct ParsedIcoFile {
    images: Vec<ParsedIcoImage>,
}

pub fn apply_icon_resource(executable_path: &Path, icon_path: &Path) -> Result<()> {
    let icon_bytes = fs::read(icon_path).map_err(|error| Bat2PeError::io(icon_path, &error))?;
    let parsed = ParsedIcoFile::parse(icon_path, &icon_bytes)?;
    apply_icon_resource_from_parsed(executable_path, &parsed)
}

pub fn apply_execution_level_manifest(executable_path: &Path, uac: bool) -> Result<()> {
    let manifest = execution_level_manifest_bytes(uac);
    apply_manifest_resource(executable_path, manifest)
}

pub fn apply_version_resource(executable_path: &Path, version_info: &VersionInfo) -> Result<()> {
    if !has_version_resource_data(version_info) {
        return Ok(());
    }

    let bytes = build_version_resource_bytes(version_info)?;
    apply_binary_resource(
        executable_path,
        RT_VERSION,
        VERSION_RESOURCE_ID,
        VERSION_RESOURCE_LANGUAGE,
        &bytes,
        "version resource",
    )
}

pub fn apply_executable_subsystem(executable_path: &Path, window_mode: WindowMode) -> Result<()> {
    let mut file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(executable_path)
        .map_err(|error| Bat2PeError::io(executable_path, &error))?;

    patch_pe_subsystem(&mut file, subsystem_for_window_mode(window_mode))
        .map_err(|error| error.with_path(executable_path.to_path_buf()))
}

impl ParsedIcoFile {
    fn parse(icon_path: &Path, bytes: &[u8]) -> Result<Self> {
        if bytes.len() < 6 {
            return Err(invalid_icon(icon_path, "icon file is too small"));
        }

        let reserved = read_u16(bytes, 0)?;
        let icon_type = read_u16(bytes, 2)?;
        let count = read_u16(bytes, 4)? as usize;
        if reserved != 0 || icon_type != 1 || count == 0 {
            return Err(invalid_icon(
                icon_path,
                "icon file header is invalid or contains no images",
            ));
        }

        let directory_len = 6usize
            .checked_add(
                count
                    .checked_mul(16)
                    .ok_or_else(|| invalid_icon(icon_path, "icon directory length overflowed"))?,
            )
            .ok_or_else(|| invalid_icon(icon_path, "icon directory length overflowed"))?;
        if bytes.len() < directory_len {
            return Err(invalid_icon(icon_path, "icon directory is truncated"));
        }

        let mut images = Vec::with_capacity(count);
        for index in 0..count {
            let entry_offset = 6 + index * 16;
            let width = bytes[entry_offset];
            let height = bytes[entry_offset + 1];
            let color_count = bytes[entry_offset + 2];
            let reserved = bytes[entry_offset + 3];
            let planes = read_u16(bytes, entry_offset + 4)?;
            let bit_count = read_u16(bytes, entry_offset + 6)?;
            let bytes_in_res = read_u32(bytes, entry_offset + 8)? as usize;
            let image_offset = read_u32(bytes, entry_offset + 12)? as usize;

            if bytes_in_res == 0 {
                return Err(invalid_icon(
                    icon_path,
                    format!("icon image {} is empty", index + 1),
                ));
            }

            let image_end = image_offset.checked_add(bytes_in_res).ok_or_else(|| {
                invalid_icon(
                    icon_path,
                    format!("icon image {} length overflowed", index + 1),
                )
            })?;
            if image_end > bytes.len() {
                return Err(invalid_icon(
                    icon_path,
                    format!("icon image {} points outside the file", index + 1),
                ));
            }

            images.push(ParsedIcoImage {
                width,
                height,
                color_count,
                reserved,
                planes,
                bit_count,
                bytes: bytes[image_offset..image_end].to_vec(),
            });
        }

        Ok(Self { images })
    }

    fn group_resource_bytes(&self) -> Result<Vec<u8>> {
        let image_count: u16 =
            self.images.len().try_into().map_err(|_| {
                Bat2PeError::new(ERR_INVALID_INPUT, "icon contains too many images")
            })?;

        let mut bytes = Vec::with_capacity(6 + self.images.len() * 14);
        bytes.extend_from_slice(&0u16.to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&image_count.to_le_bytes());

        for (index, image) in self.images.iter().enumerate() {
            let resource_id: u16 = (index + 1).try_into().map_err(|_| {
                Bat2PeError::new(ERR_INVALID_INPUT, "icon contains too many images")
            })?;
            let image_size: u32 = image
                .bytes
                .len()
                .try_into()
                .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "icon image is too large"))?;

            bytes.push(image.width);
            bytes.push(image.height);
            bytes.push(image.color_count);
            bytes.push(image.reserved);
            bytes.extend_from_slice(&image.planes.to_le_bytes());
            bytes.extend_from_slice(&image.bit_count.to_le_bytes());
            bytes.extend_from_slice(&image_size.to_le_bytes());
            bytes.extend_from_slice(&resource_id.to_le_bytes());
        }

        Ok(bytes)
    }
}

fn invalid_icon(icon_path: &Path, message: impl Into<String>) -> Bat2PeError {
    Bat2PeError::new(ERR_INVALID_INPUT, message).with_path(icon_path.to_path_buf())
}

fn read_u16(bytes: &[u8], offset: usize) -> Result<u16> {
    let end = offset
        .checked_add(2)
        .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "icon offset overflowed"))?;
    let slice = bytes
        .get(offset..end)
        .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "icon data is truncated"))?;
    Ok(u16::from_le_bytes([slice[0], slice[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> Result<u32> {
    let end = offset
        .checked_add(4)
        .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "icon offset overflowed"))?;
    let slice = bytes
        .get(offset..end)
        .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "icon data is truncated"))?;
    Ok(u32::from_le_bytes([slice[0], slice[1], slice[2], slice[3]]))
}

fn execution_level_manifest_bytes(uac: bool) -> &'static [u8] {
    if uac {
        REQUIRE_ADMINISTRATOR_MANIFEST.as_bytes()
    } else {
        AS_INVOKER_MANIFEST.as_bytes()
    }
}

fn subsystem_for_window_mode(window_mode: WindowMode) -> u16 {
    match window_mode {
        WindowMode::Visible => IMAGE_SUBSYSTEM_WINDOWS_CUI,
        WindowMode::Hidden => IMAGE_SUBSYSTEM_WINDOWS_GUI,
    }
}

fn patch_pe_subsystem<T>(file: &mut T, subsystem: u16) -> Result<()>
where
    T: Read + Write + Seek,
{
    file.seek(SeekFrom::Start(0))
        .map_err(|error| invalid_executable_io(error, "failed to read DOS header"))?;

    let mut dos_header = [0u8; 64];
    file.read_exact(&mut dos_header)
        .map_err(|error| invalid_executable_io(error, "failed to read DOS header"))?;

    if u16::from_le_bytes([dos_header[0], dos_header[1]]) != DOS_SIGNATURE {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "invalid executable DOS signature",
        ));
    }

    let pe_offset = u32::from_le_bytes([
        dos_header[0x3C],
        dos_header[0x3D],
        dos_header[0x3E],
        dos_header[0x3F],
    ]) as u64;
    let signature_offset = pe_offset;
    let subsystem_offset = pe_offset + 4 + 20 + PE_SUBSYSTEM_OFFSET;

    file.seek(SeekFrom::Start(signature_offset))
        .map_err(|error| invalid_executable_io(error, "failed to seek to PE header"))?;

    let mut signature = [0u8; 4];
    file.read_exact(&mut signature)
        .map_err(|error| invalid_executable_io(error, "failed to read PE signature"))?;
    if u32::from_le_bytes(signature) != PE_SIGNATURE {
        return Err(Bat2PeError::new(
            ERR_INVALID_EXECUTABLE,
            "invalid PE signature",
        ));
    }

    file.seek(SeekFrom::Start(subsystem_offset))
        .map_err(|error| invalid_executable_io(error, "failed to seek to subsystem field"))?;
    file.write_all(&subsystem.to_le_bytes())
        .map_err(|error| invalid_executable_io(error, "failed to write subsystem field"))?;
    file.flush()
        .map_err(|error| invalid_executable_io(error, "failed to flush subsystem update"))?;
    Ok(())
}

fn invalid_executable_io(error: std::io::Error, message: &str) -> Bat2PeError {
    Bat2PeError::new(ERR_INVALID_EXECUTABLE, message).with_details(error.to_string())
}

#[cfg(windows)]
fn apply_manifest_resource(executable_path: &Path, manifest: &[u8]) -> Result<()> {
    apply_binary_resource(
        executable_path,
        RT_MANIFEST,
        MANIFEST_RESOURCE_ID,
        0,
        manifest,
        "execution manifest resource",
    )
}

#[cfg(not(windows))]
fn apply_manifest_resource(_executable_path: &Path, _manifest: &[u8]) -> Result<()> {
    Err(Bat2PeError::new(
        ERR_INVALID_INPUT,
        "manifest resource updates are only supported on Windows",
    ))
}

#[cfg(windows)]
fn apply_icon_resource_from_parsed(executable_path: &Path, parsed: &ParsedIcoFile) -> Result<()> {
    use windows_sys::Win32::System::LibraryLoader::{
        BeginUpdateResourceW, EndUpdateResourceW, UpdateResourceW,
    };

    let executable_path_wide = to_wide(executable_path);
    let update_handle = unsafe { BeginUpdateResourceW(executable_path_wide.as_ptr(), 0) };
    if update_handle.is_null() {
        return Err(win32_error(
            executable_path,
            "failed to begin updating executable resources",
        ));
    }

    let group_resource = parsed.group_resource_bytes()?;
    let update_result = (|| {
        for (index, image) in parsed.images.iter().enumerate() {
            let resource_id: u16 = (index + 1).try_into().map_err(|_| {
                Bat2PeError::new(ERR_INVALID_INPUT, "icon contains too many images")
            })?;
            let image_size: u32 = image
                .bytes
                .len()
                .try_into()
                .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "icon image is too large"))?;

            let updated = unsafe {
                UpdateResourceW(
                    update_handle,
                    make_int_resource(RT_ICON),
                    make_int_resource(resource_id),
                    0,
                    image.bytes.as_ptr().cast(),
                    image_size,
                )
            };
            if updated == 0 {
                return Err(win32_error(
                    executable_path,
                    format!("failed to write icon image resource {}", index + 1),
                ));
            }
        }

        let group_updated = unsafe {
            UpdateResourceW(
                update_handle,
                make_int_resource(RT_GROUP_ICON),
                make_int_resource(GROUP_ICON_RESOURCE_ID),
                0,
                group_resource.as_ptr().cast(),
                group_resource.len().try_into().map_err(|_| {
                    Bat2PeError::new(ERR_INVALID_INPUT, "group icon resource is too large")
                })?,
            )
        };
        if group_updated == 0 {
            return Err(win32_error(
                executable_path,
                "failed to write group icon resource",
            ));
        }

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

fn has_version_resource_data(version_info: &VersionInfo) -> bool {
    version_info.company_name.is_some()
        || version_info.product_name.is_some()
        || version_info.file_description.is_some()
        || version_info.file_version.is_some()
        || version_info.product_version.is_some()
        || version_info.original_filename.is_some()
        || version_info.internal_name.is_some()
}

fn build_version_resource_bytes(version_info: &VersionInfo) -> Result<Vec<u8>> {
    let fixed_info = build_fixed_file_info_bytes(version_info);
    let mut children = Vec::new();

    let string_entries = version_string_entries(version_info);
    if !string_entries.is_empty() {
        let string_children: Result<Vec<Vec<u8>>> = string_entries
            .iter()
            .map(|(key, value)| build_string_block(key, value))
            .collect();
        let string_table = build_version_block(
            VERSION_STRING_LANGUAGE_CODEPAGE,
            0,
            1,
            &[],
            &string_children?,
        )?;
        children.push(build_version_block(
            "StringFileInfo",
            0,
            1,
            &[],
            &[string_table],
        )?);
    }

    let translation = build_translation_value_bytes(&[(VERSION_RESOURCE_LANGUAGE, 0x04B0)]);
    let var = build_version_block(
        "Translation",
        u16::try_from(translation.len()).map_err(|_| {
            Bat2PeError::new(ERR_INVALID_INPUT, "translation resource is too large")
        })?,
        0,
        &translation,
        &[],
    )?;
    children.push(build_version_block("VarFileInfo", 0, 1, &[], &[var])?);

    build_version_block(
        "VS_VERSION_INFO",
        u16::try_from(fixed_info.len())
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "fixed version info is too large"))?,
        0,
        &fixed_info,
        &children,
    )
}

fn build_fixed_file_info_bytes(version_info: &VersionInfo) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(13 * std::mem::size_of::<u32>());
    let file_version = version_info.file_version.clone().unwrap_or(VersionTriplet {
        major: 0,
        minor: 0,
        patch: 0,
    });
    let product_version = version_info
        .product_version
        .clone()
        .unwrap_or(VersionTriplet {
            major: 0,
            minor: 0,
            patch: 0,
        });

    push_u32(&mut bytes, VS_FFI_SIGNATURE);
    push_u32(&mut bytes, VS_FFI_STRUC_VERSION);
    push_u32(
        &mut bytes,
        ((file_version.major as u32) << 16) | file_version.minor as u32,
    );
    push_u32(&mut bytes, (file_version.patch as u32) << 16);
    push_u32(
        &mut bytes,
        ((product_version.major as u32) << 16) | product_version.minor as u32,
    );
    push_u32(&mut bytes, (product_version.patch as u32) << 16);
    push_u32(&mut bytes, VS_FFI_FILEFLAGSMASK);
    push_u32(&mut bytes, 0);
    push_u32(&mut bytes, VOS_NT_WINDOWS32);
    push_u32(&mut bytes, VFT_APP);
    push_u32(&mut bytes, 0);
    push_u32(&mut bytes, 0);
    push_u32(&mut bytes, 0);
    bytes
}

fn version_string_entries(version_info: &VersionInfo) -> Vec<(&'static str, String)> {
    let mut entries = Vec::new();

    if let Some(value) = &version_info.company_name {
        entries.push(("CompanyName", value.clone()));
    }
    if let Some(value) = &version_info.file_description {
        entries.push(("FileDescription", value.clone()));
    }
    if let Some(value) = &version_info.file_version {
        entries.push(("FileVersion", value.to_string()));
    }
    if let Some(value) = &version_info.internal_name {
        entries.push(("InternalName", value.clone()));
    }
    if let Some(value) = &version_info.original_filename {
        entries.push(("OriginalFilename", value.clone()));
    }
    if let Some(value) = &version_info.product_name {
        entries.push(("ProductName", value.clone()));
    }
    if let Some(value) = &version_info.product_version {
        entries.push(("ProductVersion", value.to_string()));
    }

    entries
}

fn build_string_block(key: &str, value: &str) -> Result<Vec<u8>> {
    let value_utf16 = utf16_bytes_with_nul(value);
    build_version_block(
        key,
        u16::try_from(value_utf16.len() / 2)
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "version string is too long"))?,
        1,
        &value_utf16,
        &[],
    )
}

fn build_translation_value_bytes(translations: &[(u16, u16)]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(translations.len() * 4);
    for (language, codepage) in translations {
        push_u16(&mut bytes, *language);
        push_u16(&mut bytes, *codepage);
    }
    bytes
}

fn build_version_block(
    key: &str,
    value_length: u16,
    value_type: u16,
    value_bytes: &[u8],
    children: &[Vec<u8>],
) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    push_u16(&mut bytes, 0);
    push_u16(&mut bytes, value_length);
    push_u16(&mut bytes, value_type);
    bytes.extend_from_slice(&utf16_bytes_with_nul(key));
    align_dword(&mut bytes);
    bytes.extend_from_slice(value_bytes);
    align_dword(&mut bytes);
    for child in children {
        bytes.extend_from_slice(child);
    }

    let block_length: u16 = bytes
        .len()
        .try_into()
        .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "version resource block is too large"))?;
    write_u16_at(&mut bytes, 0, block_length);
    Ok(bytes)
}

fn utf16_bytes_with_nul(value: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    for code_unit in value.encode_utf16().chain(std::iter::once(0)) {
        push_u16(&mut bytes, code_unit);
    }
    bytes
}

fn align_dword(bytes: &mut Vec<u8>) {
    while bytes.len() % 4 != 0 {
        bytes.push(0);
    }
}

fn push_u16(bytes: &mut Vec<u8>, value: u16) {
    bytes.extend_from_slice(&value.to_le_bytes());
}

fn push_u32(bytes: &mut Vec<u8>, value: u32) {
    bytes.extend_from_slice(&value.to_le_bytes());
}

fn write_u16_at(bytes: &mut [u8], offset: usize, value: u16) {
    bytes[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
}

#[cfg(windows)]
fn apply_binary_resource(
    executable_path: &Path,
    resource_type: u16,
    resource_id: u16,
    language: u16,
    bytes: &[u8],
    label: &str,
) -> Result<()> {
    use windows_sys::Win32::System::LibraryLoader::{
        BeginUpdateResourceW, EndUpdateResourceW, UpdateResourceW,
    };

    let executable_path_wide = to_wide(executable_path);
    let update_handle = unsafe { BeginUpdateResourceW(executable_path_wide.as_ptr(), 0) };
    if update_handle.is_null() {
        return Err(win32_error(
            executable_path,
            "failed to begin updating executable resources",
        ));
    }

    let update_result = (|| {
        let size: u32 = bytes
            .len()
            .try_into()
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, format!("{label} is too large")))?;

        let updated = unsafe {
            UpdateResourceW(
                update_handle,
                make_int_resource(resource_type),
                make_int_resource(resource_id),
                language,
                bytes.as_ptr().cast_mut().cast(),
                size,
            )
        };
        if updated == 0 {
            return Err(win32_error(
                executable_path,
                format!("failed to write {label}"),
            ));
        }

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

#[cfg(not(windows))]
fn apply_binary_resource(
    _executable_path: &Path,
    _resource_type: u16,
    _resource_id: u16,
    _language: u16,
    _bytes: &[u8],
    _label: &str,
) -> Result<()> {
    Err(Bat2PeError::new(
        ERR_INVALID_INPUT,
        "resource updates are only supported on Windows",
    ))
}

#[cfg(not(windows))]
fn apply_icon_resource_from_parsed(_executable_path: &Path, _parsed: &ParsedIcoFile) -> Result<()> {
    Err(Bat2PeError::new(
        ERR_INVALID_INPUT,
        "icon resource updates are only supported on Windows",
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
    Bat2PeError::new(ERR_IO, message)
        .with_path(path.to_path_buf())
        .with_details(error.to_string())
}

#[cfg(test)]
mod tests {
    use std::io::{Cursor, Seek, SeekFrom};

    use super::{
        IMAGE_SUBSYSTEM_WINDOWS_GUI, build_version_resource_bytes, execution_level_manifest_bytes,
        patch_pe_subsystem,
    };
    use crate::error::ERR_INVALID_EXECUTABLE;
    use crate::model::{VersionInfo, VersionTriplet};

    fn fake_pe_bytes() -> Vec<u8> {
        let mut bytes = vec![0u8; 512];
        bytes[0..2].copy_from_slice(b"MZ");
        bytes[0x3C..0x40].copy_from_slice(&0x80u32.to_le_bytes());
        bytes[0x80..0x84].copy_from_slice(b"PE\0\0");
        bytes
    }

    #[test]
    fn uses_as_invoker_manifest_when_uac_is_disabled() {
        let manifest = String::from_utf8(execution_level_manifest_bytes(false).to_vec())
            .expect("manifest should be utf-8");
        assert!(manifest.contains("requestedExecutionLevel"));
        assert!(manifest.contains("asInvoker"));
    }

    #[test]
    fn uses_require_administrator_manifest_when_uac_is_enabled() {
        let manifest = String::from_utf8(execution_level_manifest_bytes(true).to_vec())
            .expect("manifest should be utf-8");
        assert!(manifest.contains("requestedExecutionLevel"));
        assert!(manifest.contains("requireAdministrator"));
    }

    #[test]
    fn version_resource_contains_expected_strings() {
        let bytes = build_version_resource_bytes(&VersionInfo {
            company_name: Some("Acme".to_string()),
            product_name: Some("Bat2PE".to_string()),
            file_description: Some("Batch launcher".to_string()),
            file_version: Some(VersionTriplet {
                major: 1,
                minor: 2,
                patch: 3,
            }),
            product_version: Some(VersionTriplet {
                major: 4,
                minor: 5,
                patch: 6,
            }),
            original_filename: Some("bat2pe.exe".to_string()),
            internal_name: Some("bat2pe".to_string()),
        })
        .expect("version resource");

        let utf16 = String::from_utf16_lossy(
            &bytes
                .chunks_exact(2)
                .map(|chunk| u16::from_le_bytes([chunk[0], chunk[1]]))
                .collect::<Vec<_>>(),
        );
        assert!(utf16.contains("VS_VERSION_INFO"));
        assert!(utf16.contains("CompanyName"));
        assert!(utf16.contains("Acme"));
        assert!(utf16.contains("FileVersion"));
        assert!(utf16.contains("1.2.3"));
        assert!(utf16.contains("ProductVersion"));
        assert!(utf16.contains("4.5.6"));
    }

    #[test]
    fn patch_pe_subsystem_updates_optional_header() {
        let mut cursor = Cursor::new(fake_pe_bytes());

        patch_pe_subsystem(&mut cursor, IMAGE_SUBSYSTEM_WINDOWS_GUI).expect("patch subsystem");
        cursor
            .seek(SeekFrom::Start(0x80 + 4 + 20 + 68))
            .expect("seek to subsystem");

        let mut subsystem = [0u8; 2];
        use std::io::Read;
        cursor
            .read_exact(&mut subsystem)
            .expect("read subsystem bytes");
        assert_eq!(u16::from_le_bytes(subsystem), IMAGE_SUBSYSTEM_WINDOWS_GUI);
    }

    #[test]
    fn patch_pe_subsystem_rejects_invalid_headers() {
        let mut cursor = Cursor::new(vec![0u8; 128]);
        let error = patch_pe_subsystem(&mut cursor, IMAGE_SUBSYSTEM_WINDOWS_GUI)
            .expect_err("invalid executable");
        assert_eq!(error.code, ERR_INVALID_EXECUTABLE);
    }
}
