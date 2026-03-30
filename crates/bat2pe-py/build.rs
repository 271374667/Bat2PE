use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn main() {
    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let workspace_root = manifest_dir
        .parent()
        .and_then(Path::parent)
        .expect("workspace root")
        .to_path_buf();
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("out dir"));
    let cargo = env::var_os("CARGO").unwrap_or_else(|| OsString::from("cargo"));
    let profile = env::var("PROFILE").expect("profile");
    let target = env::var("TARGET").expect("target");

    emit_rerun_if_changed(&workspace_root.join("Cargo.toml"));
    emit_rerun_if_changed(&workspace_root.join("Cargo.lock"));
    emit_rerun_tree(&workspace_root.join("crates").join("bat2pe-core"));
    emit_rerun_tree(&workspace_root.join("crates").join("bat2pe-runtime-host"));
    println!("cargo:rerun-if-env-changed=CARGO");
    println!("cargo:rerun-if-env-changed=PROFILE");
    println!("cargo:rerun-if-env-changed=TARGET");

    let host_target_dir = out_dir.join("embedded-runtime-host-target");
    let host_output_dir = if profile == "release" {
        host_target_dir.join(&target).join("release")
    } else if profile == "debug" {
        host_target_dir.join(&target).join("debug")
    } else {
        host_target_dir.join(&target).join(&profile)
    };
    let host_executable = host_output_dir.join("bat2pe-runtime-host.exe");

    let mut command = Command::new(cargo);
    command
        .arg("build")
        .arg("--manifest-path")
        .arg(workspace_root.join("Cargo.toml"))
        .arg("-p")
        .arg("bat2pe-runtime-host")
        .arg("--target")
        .arg(&target)
        .arg("--target-dir")
        .arg(&host_target_dir);

    if profile == "release" {
        command.arg("--release");
    } else if profile != "debug" {
        command.arg("--profile").arg(&profile);
    }

    let output = command.output().expect("build embedded runtime host");
    if !output.status.success() {
        panic!(
            "failed to build embedded runtime host\nstdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
    }

    if !host_executable.exists() {
        panic!(
            "embedded runtime host executable was not produced: {}",
            host_executable.display()
        );
    }

    println!(
        "cargo:rustc-env=BAT2PE_EMBEDDED_RUNTIME_HOST_PATH={}",
        host_executable.display()
    );
    println!("cargo:rustc-env=BAT2PE_EMBEDDED_RUNTIME_HOST_LABEL=embedded-bat2pe-runtime-host.exe");
}

fn emit_rerun_tree(path: &Path) {
    if path.is_file() {
        emit_rerun_if_changed(path);
        return;
    }

    let entries = match fs::read_dir(path) {
        Ok(entries) => entries,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let entry_path = entry.path();
        if entry_path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name == "target")
        {
            continue;
        }

        if entry_path.is_dir() {
            emit_rerun_tree(&entry_path);
        } else {
            emit_rerun_if_changed(&entry_path);
        }
    }
}

fn emit_rerun_if_changed(path: &Path) {
    if path.exists() {
        println!("cargo:rerun-if-changed={}", path.display());
    }
}
