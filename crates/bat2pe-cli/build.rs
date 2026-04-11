use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const RT_ICON: u16 = 3;
const RT_GROUP_ICON: u16 = 14;
const GROUP_ICON_RESOURCE_ID: u16 = 1;
const ICON_RESOURCE_MEMORY_FLAGS: u16 = 0x1030;

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

fn main() {
    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let target = env::var("TARGET").expect("target");
    let icon_path = manifest_dir
        .parent()
        .expect("workspace crates dir")
        .join("bat2pe-core")
        .join("assets")
        .join("default-icon.ico");

    println!("cargo:rerun-if-changed={}", icon_path.display());
    if !target.contains("windows-msvc") {
        return;
    }

    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("out dir"));
    let resource_path = out_dir.join("bat2pe-default-icon.res");
    write_icon_resource_file(&icon_path, &resource_path).expect("write bat2pe icon resource");
    println!(
        "cargo:rustc-link-arg-bin=bat2pe={}",
        resource_path.display()
    );
}

fn write_icon_resource_file(icon_path: &Path, resource_path: &Path) -> Result<(), String> {
    let icon_bytes = fs::read(icon_path)
        .map_err(|error| format!("failed to read icon {}: {error}", icon_path.display()))?;
    let images = parse_ico(icon_path, &icon_bytes)?;
    let group_icon = build_group_icon_resource(&images)?;

    let mut resource_file = Vec::new();
    write_resource_entry(&mut resource_file, 0, 0, 0, 0, &[]);
    for (index, image) in images.iter().enumerate() {
        write_resource_entry(
            &mut resource_file,
            RT_ICON,
            u16::try_from(index + 1).map_err(|_| "icon contains too many images".to_string())?,
            0,
            ICON_RESOURCE_MEMORY_FLAGS,
            &image.bytes,
        );
    }
    write_resource_entry(
        &mut resource_file,
        RT_GROUP_ICON,
        GROUP_ICON_RESOURCE_ID,
        0,
        ICON_RESOURCE_MEMORY_FLAGS,
        &group_icon,
    );

    fs::write(resource_path, resource_file).map_err(|error| {
        format!(
            "failed to write resource file {}: {error}",
            resource_path.display()
        )
    })?;
    Ok(())
}

fn parse_ico(icon_path: &Path, bytes: &[u8]) -> Result<Vec<ParsedIcoImage>, String> {
    if bytes.len() < 6 {
        return Err(format!("icon file is too small: {}", icon_path.display()));
    }

    let reserved = read_u16(bytes, 0)?;
    let icon_type = read_u16(bytes, 2)?;
    let count = read_u16(bytes, 4)? as usize;
    if reserved != 0 || icon_type != 1 || count == 0 {
        return Err(format!(
            "icon file header is invalid or contains no images: {}",
            icon_path.display()
        ));
    }

    let directory_len = 6usize
        .checked_add(
            count
                .checked_mul(16)
                .ok_or_else(|| "icon directory length overflowed".to_string())?,
        )
        .ok_or_else(|| "icon directory length overflowed".to_string())?;
    if bytes.len() < directory_len {
        return Err(format!(
            "icon directory is truncated: {}",
            icon_path.display()
        ));
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
            return Err(format!("icon image {} is empty", index + 1));
        }

        let image_end = image_offset
            .checked_add(bytes_in_res)
            .ok_or_else(|| format!("icon image {} length overflowed", index + 1))?;
        if image_end > bytes.len() {
            return Err(format!("icon image {} points outside the file", index + 1));
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

    Ok(images)
}

fn build_group_icon_resource(images: &[ParsedIcoImage]) -> Result<Vec<u8>, String> {
    let image_count: u16 = images
        .len()
        .try_into()
        .map_err(|_| "icon contains too many images".to_string())?;

    let mut bytes = Vec::with_capacity(6 + images.len() * 14);
    bytes.extend_from_slice(&0u16.to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&image_count.to_le_bytes());

    for (index, image) in images.iter().enumerate() {
        let resource_id: u16 = (index + 1)
            .try_into()
            .map_err(|_| "icon contains too many images".to_string())?;
        let image_size: u32 = image
            .bytes
            .len()
            .try_into()
            .map_err(|_| "icon image is too large".to_string())?;

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

fn write_resource_entry(
    resource_file: &mut Vec<u8>,
    resource_type: u16,
    resource_name: u16,
    language: u16,
    memory_flags: u16,
    data: &[u8],
) {
    let mut header = Vec::new();
    push_ordinal(&mut header, resource_type);
    push_ordinal(&mut header, resource_name);
    align_dword(&mut header);
    header.extend_from_slice(&0u32.to_le_bytes());
    header.extend_from_slice(&memory_flags.to_le_bytes());
    header.extend_from_slice(&language.to_le_bytes());
    header.extend_from_slice(&0u32.to_le_bytes());
    header.extend_from_slice(&0u32.to_le_bytes());

    let header_size = u32::try_from(header.len() + 8).expect("resource header too large");
    let data_size = u32::try_from(data.len()).expect("resource data too large");

    resource_file.extend_from_slice(&data_size.to_le_bytes());
    resource_file.extend_from_slice(&header_size.to_le_bytes());
    resource_file.extend_from_slice(&header);
    resource_file.extend_from_slice(data);
    align_dword(resource_file);
}

fn push_ordinal(bytes: &mut Vec<u8>, value: u16) {
    bytes.extend_from_slice(&0xFFFFu16.to_le_bytes());
    bytes.extend_from_slice(&value.to_le_bytes());
}

fn align_dword(bytes: &mut Vec<u8>) {
    while bytes.len() % 4 != 0 {
        bytes.push(0);
    }
}

fn read_u16(bytes: &[u8], offset: usize) -> Result<u16, String> {
    let slice = bytes
        .get(offset..offset + 2)
        .ok_or_else(|| "icon data is truncated".to_string())?;
    Ok(u16::from_le_bytes([slice[0], slice[1]]))
}

fn read_u32(bytes: &[u8], offset: usize) -> Result<u32, String> {
    let slice = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| "icon data is truncated".to_string())?;
    Ok(u32::from_le_bytes([slice[0], slice[1], slice[2], slice[3]]))
}
