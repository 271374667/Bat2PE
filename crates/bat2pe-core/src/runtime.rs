#[cfg(windows)]
mod imp {
    use std::env;
    use std::fs::{self, File};
    use std::io::Write;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::process::CommandExt;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};
    use std::time::{SystemTime, UNIX_EPOCH};

    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_TEMPORARY, SetFileAttributesW,
    };
    use windows_sys::Win32::System::Console::{ATTACH_PARENT_PROCESS, AttachConsole, FreeConsole};
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{MB_ICONERROR, MB_OK, MessageBoxW};

    use crate::error::{Bat2PeError, ERR_DIRECTORY_NOT_WRITABLE, ERR_INVALID_EXECUTABLE, Result};
    use crate::model::WindowMode;
    use crate::overlay::read_overlay_from_path;

    pub fn run_console() -> Result<i32> {
        run(WindowMode::Visible)
    }

    pub fn run_windows() -> Result<i32> {
        run(WindowMode::Hidden)
    }

    struct TempScript {
        path: PathBuf,
    }

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

        let temp_script = create_temp_script(exe_directory, &overlay.script_bytes)?;
        spawn_cmd(mode, &temp_script.path)
    }

    fn create_temp_script(exe_directory: &Path, script_bytes: &[u8]) -> Result<TempScript> {
        let file_name = format!(
            "bat2pe-{}-{}.cmd",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
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
        mark_hidden_temporary(&path);
        Ok(TempScript { path })
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

    fn wide_from_path(path: &Path) -> Vec<u16> {
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
    }

    fn wide_from_str(value: &str) -> Vec<u16> {
        std::ffi::OsStr::new(value)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect()
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
