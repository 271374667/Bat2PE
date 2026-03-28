use std::fs;
use std::path::Path;

use crate::error::{Bat2PeError, ERR_INVALID_INPUT, ERR_IO, Result};

const RT_ICON: u16 = 3;
const RT_GROUP_ICON: u16 = 14;
const GROUP_ICON_RESOURCE_ID: u16 = 1;

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
