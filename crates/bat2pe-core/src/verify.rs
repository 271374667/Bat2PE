use std::path::Path;
use std::process::{Command, Output};

use crate::error::{Bat2PeError, ERR_VERIFY_UAC_INTERACTIVE, Result};
use crate::inspect::inspect_executable;
use crate::model::{VerifyExecution, VerifyRequest, VerifyResult};

pub fn verify(request: &VerifyRequest) -> Result<VerifyResult> {
    let inspect = inspect_executable(&request.exe_path)?;
    if inspect.runtime.uac {
        return Err(Bat2PeError::new(
            ERR_VERIFY_UAC_INTERACTIVE,
            "verify does not support uac-enabled executables because Windows elevation is interactive",
        )
        .with_path(request.exe_path.clone()));
    }

    let script_output = run_script(request)?;
    let exe_output = run_executable(request)?;

    let exit_code_match = normalized_exit_code(&script_output) == normalized_exit_code(&exe_output);
    let stderr_match = script_output.stderr == exe_output.stderr;

    Ok(VerifyResult {
        script: output_to_execution(script_output),
        executable: output_to_execution(exe_output),
        exit_code_match,
        stderr_match,
        success: exit_code_match && stderr_match,
    })
}

fn run_script(request: &VerifyRequest) -> Result<Output> {
    let mut command = Command::new("cmd.exe");
    command.arg("/d").arg("/c").arg(&request.script_path);
    command.args(&request.arguments);
    configure_working_dir(&mut command, request.working_dir.as_deref());
    command
        .output()
        .map_err(|error| Bat2PeError::io(&request.script_path, &error))
}

fn run_executable(request: &VerifyRequest) -> Result<Output> {
    let mut command = Command::new(&request.exe_path);
    command.args(&request.arguments);
    configure_working_dir(&mut command, request.working_dir.as_deref());
    command
        .output()
        .map_err(|error| Bat2PeError::io(&request.exe_path, &error))
}

fn configure_working_dir(command: &mut Command, working_dir: Option<&Path>) {
    if let Some(path) = working_dir {
        command.current_dir(path);
    }
}

fn normalized_exit_code(output: &Output) -> i32 {
    output.status.code().unwrap_or(1)
}

fn output_to_execution(output: Output) -> VerifyExecution {
    VerifyExecution {
        exit_code: output.status.code().unwrap_or(1),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
    }
}
