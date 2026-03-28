#[cfg(windows)]
mod imp {
    use std::env;
    use std::ffi::OsString;
    use std::fs::{self, File};
    use std::io::Write;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::thread;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_TEMPORARY, SetFileAttributesW,
    };
    use windows_sys::Win32::System::Console::{ATTACH_PARENT_PROCESS, AttachConsole, FreeConsole};
    use windows_sys::Win32::System::Threading::{
        CREATE_NO_WINDOW, GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
        WaitForSingleObject,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};

    use crate::error::{Bat2PeError, ERR_DIRECTORY_NOT_WRITABLE, ERR_INVALID_EXECUTABLE, Result};
    use crate::model::WindowMode;
    use crate::overlay::read_overlay_from_path;

    pub fn run_console() -> Result<i32> {
        if is_cleanup_helper_invocation() {
            return run_cleanup_helper();
        }
        run(WindowMode::Visible)
    }

    pub fn run_windows() -> Result<i32> {
        if is_cleanup_helper_invocation() {
            return run_cleanup_helper();
        }
        run(WindowMode::Hidden)
    }

    struct TempScript {
        path: PathBuf,
    }

    const STILL_ACTIVE_EXIT_CODE: u32 = 259;
    const SYNCHRONIZE_ACCESS: u32 = 0x0010_0000;
    const INFINITE_WAIT: u32 = 0xFFFF_FFFF;
    const CLEANUP_HELPER_ENV: &str = "BAT2PE_INTERNAL_MODE";
    const CLEANUP_HELPER_MODE: &str = "cleanup_temp_script_v1";
    const CLEANUP_HELPER_ARG: &str = "--bat2pe-internal-cleanup";
    const CLEANUP_RETRY_ATTEMPTS: usize = 100;
    const CLEANUP_RETRY_DELAY: Duration = Duration::from_millis(100);

    impl Drop for TempScript {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.path);
        }
    }

    fn run(mode: WindowMode) -> Result<i32> {
        let exe_path = env::current_exe().map_err(|error| {
            Bat2PeError::new(
                ERR_INVALID_EXECUTABLE,
                "failed to resolve current executable",
            )
            .with_details(error.to_string())
        })?;
        let overlay = read_overlay_from_path(&exe_path)?;
        let exe_directory = exe_path.parent().ok_or_else(|| {
            Bat2PeError::new(
                ERR_INVALID_EXECUTABLE,
                "executable path has no parent directory",
            )
        })?;

        cleanup_stale_temp_scripts(exe_directory);
        let temp_script = create_temp_script(
            exe_directory,
            &overlay.metadata.runtime.temp_script_suffix,
            &overlay.script_bytes,
        )?;
        spawn_cleanup_helper(&exe_path, std::process::id(), &temp_script.path);
        spawn_cmd(mode, &temp_script.path)
    }

    fn is_cleanup_helper_invocation() -> bool {
        matches!(
            env::var(CLEANUP_HELPER_ENV).ok().as_deref(),
            Some(CLEANUP_HELPER_MODE)
        ) && matches!(env::args_os().nth(1), Some(arg) if arg == OsString::from(CLEANUP_HELPER_ARG))
    }

    fn run_cleanup_helper() -> Result<i32> {
        let mut args = env::args_os().skip(1);
        match args.next() {
            Some(marker) if marker == OsString::from(CLEANUP_HELPER_ARG) => {}
            _ => {
                return Err(Bat2PeError::new(
                    ERR_INVALID_EXECUTABLE,
                    "invalid cleanup helper invocation",
                ));
            }
        }

        let owner_pid = args
            .next()
            .and_then(|value| value.to_string_lossy().parse::<u32>().ok())
            .ok_or_else(|| {
                Bat2PeError::new(ERR_INVALID_EXECUTABLE, "missing cleanup helper owner pid")
            })?;
        let script_path = args.next().map(PathBuf::from).ok_or_else(|| {
            Bat2PeError::new(ERR_INVALID_EXECUTABLE, "missing cleanup helper script path")
        })?;

        wait_for_process_exit(owner_pid);
        delete_path_with_retries(&script_path);
        Ok(0)
    }

    fn cleanup_stale_temp_scripts(exe_directory: &Path) {
        let entries = match fs::read_dir(exe_directory) {
            Ok(entries) => entries,
            Err(_) => return,
        };

        for entry in entries.flatten() {
            let path = entry.path();
            let Some(owner_pid) = extract_temp_script_owner_pid(&path) else {
                continue;
            };

            if owner_pid == std::process::id() || process_is_running(owner_pid) {
                continue;
            }

            let _ = fs::remove_file(path);
        }
    }

    fn create_temp_script(
        exe_directory: &Path,
        suffix: &str,
        script_bytes: &[u8],
    ) -> Result<TempScript> {
        let suffix = normalized_temp_script_suffix(suffix);
        let file_name = format!(
            "bat2pe-{}-{}{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos(),
            suffix,
        );
        let path = exe_directory.join(file_name);

        let mut file = match File::create(&path) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
                let message = format_directory_error(exe_directory);
                let _ = writeln!(std::io::stderr(), "{message}");
                show_message_box("Application Error", &message);
                return Err(Bat2PeError::new(
                    ERR_DIRECTORY_NOT_WRITABLE,
                    "the executable directory is not writable",
                )
                .with_path(exe_directory.to_path_buf()));
            }
            Err(error) => return Err(Bat2PeError::io(&path, &error)),
        };

        file.write_all(script_bytes)
            .map_err(|error| Bat2PeError::io(&path, &error))?;
        file.flush()
            .map_err(|error| Bat2PeError::io(&path, &error))?;
        mark_hidden_temporary(&path);
        Ok(TempScript { path })
    }

    fn spawn_cleanup_helper(exe_path: &Path, owner_pid: u32, script_path: &Path) {
        let mut command = Command::new(exe_path);
        command
            .arg(CLEANUP_HELPER_ARG)
            .arg(owner_pid.to_string())
            .arg(script_path)
            .env(CLEANUP_HELPER_ENV, CLEANUP_HELPER_MODE)
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let _ = command.spawn();
    }

    fn spawn_cmd(mode: WindowMode, script_path: &Path) -> Result<i32> {
        let mut command = Command::new("cmd.exe");
        command.arg("/d").arg("/c").arg(script_path);
        command.args(env::args_os().skip(1));

        let mut attached_parent = false;
        match mode {
            WindowMode::Visible => inherit_stdio(&mut command),
            WindowMode::Hidden => {
                attached_parent = unsafe { AttachConsole(ATTACH_PARENT_PROCESS) } != 0;
                if attached_parent {
                    inherit_stdio(&mut command);
                } else {
                    command.creation_flags(CREATE_NO_WINDOW);
                    command.stdin(Stdio::null());
                    command.stdout(Stdio::null());
                    command.stderr(Stdio::null());
                }
            }
        }

        let status = command
            .status()
            .map_err(|error| Bat2PeError::io(script_path, &error))?;

        if attached_parent {
            unsafe {
                FreeConsole();
            }
        }

        Ok(status.code().unwrap_or(1))
    }

    fn inherit_stdio(command: &mut Command) {
        command.stdin(Stdio::inherit());
        command.stdout(Stdio::inherit());
        command.stderr(Stdio::inherit());
    }

    fn format_directory_error(exe_directory: &Path) -> String {
        format!(
            "This application could not start the embedded batch script.\n\n\
The executable directory is not writable:\n{}\n\n\
To preserve %~dp0 semantics, the runtime script must be created in the executable directory.\n\n\
The application will now exit.",
            exe_directory.display()
        )
    }

    fn mark_hidden_temporary(path: &Path) {
        let wide = wide_from_path(path);
        unsafe {
            SetFileAttributesW(
                wide.as_ptr(),
                FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_TEMPORARY,
            );
        }
    }

    fn normalized_temp_script_suffix(suffix: &str) -> &'static str {
        if suffix.eq_ignore_ascii_case(".bat") {
            ".bat"
        } else {
            ".cmd"
        }
    }

    fn extract_temp_script_owner_pid(path: &Path) -> Option<u32> {
        let file_name = path.file_name()?.to_str()?;
        let suffix = if file_name
            .get(file_name.len().saturating_sub(4)..)?
            .eq_ignore_ascii_case(".cmd")
        {
            ".cmd"
        } else if file_name
            .get(file_name.len().saturating_sub(4)..)?
            .eq_ignore_ascii_case(".bat")
        {
            ".bat"
        } else {
            return None;
        };
        let stem = file_name.strip_suffix(suffix)?;
        let stem = stem.strip_prefix("bat2pe-")?;
        let (pid, timestamp) = stem.split_once('-')?;
        pid.parse::<u32>().ok()?;
        timestamp.parse::<u128>().ok()?;
        pid.parse::<u32>().ok()
    }

    fn process_is_running(pid: u32) -> bool {
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                return false;
            }

            let mut exit_code = 0;
            let query_ok = GetExitCodeProcess(handle, &mut exit_code) != 0;
            let _ = windows_sys::Win32::Foundation::CloseHandle(handle);
            query_ok && exit_code == STILL_ACTIVE_EXIT_CODE
        }
    }

    fn wait_for_process_exit(pid: u32) {
        unsafe {
            let handle = OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE_ACCESS,
                0,
                pid,
            );
            if handle.is_null() {
                return;
            }

            let _ = WaitForSingleObject(handle, INFINITE_WAIT);
            let _ = windows_sys::Win32::Foundation::CloseHandle(handle);
        }
    }

    fn delete_path_with_retries(path: &Path) {
        for _ in 0..CLEANUP_RETRY_ATTEMPTS {
            match fs::remove_file(path) {
                Ok(()) => return,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
                Err(_) => thread::sleep(CLEANUP_RETRY_DELAY),
            }
        }
    }

    fn show_message_box(title: &str, message: &str) {
        let title = wide_from_str(title);
        let message = wide_from_str(message);
        unsafe {
            MessageBoxW(
                std::ptr::null_mut(),
                message.as_ptr(),
                title.as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
    }

    fn wide_from_str(value: &str) -> Vec<u16> {
        std::ffi::OsStr::new(value)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    fn wide_from_path(path: &Path) -> Vec<u16> {
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn unique_temp_dir() -> PathBuf {
            let path = env::temp_dir().join(format!(
                "bat2pe-runtime-test-{}-{}",
                std::process::id(),
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos()
            ));
            fs::create_dir_all(&path).expect("create temp test directory");
            path
        }

        #[test]
        fn create_temp_script_uses_cmd_suffix_and_removes_on_drop() {
            let temp_dir = unique_temp_dir();
            let temp_script =
                create_temp_script(&temp_dir, ".cmd", b"@echo off\r\necho ok\r\n").expect("script");
            let path = temp_script.path.clone();

            assert_eq!(
                path.extension()
                    .and_then(|value| value.to_str())
                    .map(|value| value.to_ascii_lowercase()),
                Some("cmd".to_string())
            );
            assert!(path.exists());

            drop(temp_script);

            assert!(!path.exists());
            fs::remove_dir_all(temp_dir).expect("remove temp test directory");
        }

        #[test]
        fn delete_path_with_retries_removes_file() {
            let temp_dir = unique_temp_dir();
            let path = temp_dir.join("bat2pe-delete-me.cmd");
            fs::write(&path, b"@echo off\r\n").expect("write temp file");

            delete_path_with_retries(&path);

            assert!(!path.exists());
            fs::remove_dir_all(temp_dir).expect("remove temp test directory");
        }

        #[test]
        fn cleanup_stale_temp_scripts_removes_orphans_only() {
            let temp_dir = unique_temp_dir();
            let stale_cmd = temp_dir.join("bat2pe-4294967294-1.cmd");
            let stale_bat = temp_dir.join("bat2pe-4294967294-2.bat");
            let keep = temp_dir.join("bat2pe-not-a-runtime-script.cmd");

            fs::write(&stale_cmd, b"@echo off\r\n").expect("write stale cmd");
            fs::write(&stale_bat, b"@echo off\r\n").expect("write stale bat");
            fs::write(&keep, b"@echo off\r\n").expect("write keep file");

            cleanup_stale_temp_scripts(&temp_dir);

            assert!(!stale_cmd.exists());
            assert!(!stale_bat.exists());
            assert!(keep.exists());

            fs::remove_file(keep).expect("remove keep file");
            fs::remove_dir_all(temp_dir).expect("remove temp test directory");
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use crate::error::{Bat2PeError, ERR_INVALID_INPUT, Result};

    pub fn run_console() -> Result<i32> {
        Err(Bat2PeError::new(
            ERR_INVALID_INPUT,
            "bat2pe runtime stubs are only supported on Windows",
        ))
    }

    pub fn run_windows() -> Result<i32> {
        Err(Bat2PeError::new(
            ERR_INVALID_INPUT,
            "bat2pe runtime stubs are only supported on Windows",
        ))
    }
}

pub fn run_console_stub() -> crate::Result<i32> {
    imp::run_console()
}

pub fn run_windows_stub() -> crate::Result<i32> {
    imp::run_windows()
}
