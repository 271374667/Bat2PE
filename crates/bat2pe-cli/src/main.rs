use std::env;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process;
use std::str::FromStr;

use bat2pe_core::{
    Bat2PeError, BuildRequest, ERR_CLI_USAGE, Result, VerifyRequest, VersionInfo, VersionTriplet,
    WindowMode, build_executable, inspect_executable, locate_stub_binaries, verify,
};
use serde::Serialize;

fn main() {
    let exit_code = match real_main() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };

    process::exit(exit_code);
}

fn real_main() -> Result<i32> {
    let mut args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.is_empty() {
        print_usage();
        return Err(usage_error("missing subcommand"));
    }

    let command = args.remove(0);
    match command.to_string_lossy().as_ref() {
        "build" => run_build(args),
        "inspect" => run_inspect(args),
        "verify" => run_verify(args),
        "help" | "-h" | "--help" => {
            print_usage();
            Ok(0)
        }
        other => Err(usage_error(format!("unknown subcommand: {other}"))),
    }
}

fn run_build(args: Vec<OsString>) -> Result<i32> {
    let mut input_bat_path: Option<PathBuf> = None;
    let mut output_exe_path: Option<PathBuf> = None;
    let mut icon_path: Option<PathBuf> = None;
    let mut version_info = VersionInfo::default();
    let mut window_mode = WindowMode::Visible;
    let mut uac = false;
    let mut quiet = false;
    let mut verbose = false;

    let mut index = 0;
    while index < args.len() {
        let key = args[index].to_string_lossy();
        match key.as_ref() {
            "--input-bat-path" => {
                input_bat_path = Some(expect_path_value(&args, index + 1, "--input-bat-path")?);
                index += 2;
            }
            "--output-exe-path" | "--out" => {
                output_exe_path = Some(expect_path_value(&args, index + 1, &key)?);
                index += 2;
            }
            "--icon-path" | "--icon" => {
                icon_path = Some(expect_path_value(&args, index + 1, &key)?);
                index += 2;
            }
            "--company" => {
                version_info.company_name =
                    Some(expect_string_value(&args, index + 1, "--company")?);
                index += 2;
            }
            "--product" => {
                version_info.product_name =
                    Some(expect_string_value(&args, index + 1, "--product")?);
                index += 2;
            }
            "--description" => {
                version_info.file_description =
                    Some(expect_string_value(&args, index + 1, "--description")?);
                index += 2;
            }
            "--file-version" => {
                version_info.file_version =
                    Some(parse_version(&args, index + 1, "--file-version")?);
                index += 2;
            }
            "--product-version" => {
                version_info.product_version =
                    Some(parse_version(&args, index + 1, "--product-version")?);
                index += 2;
            }
            "--original-filename" => {
                version_info.original_filename = Some(expect_string_value(
                    &args,
                    index + 1,
                    "--original-filename",
                )?);
                index += 2;
            }
            "--internal-name" => {
                version_info.internal_name =
                    Some(expect_string_value(&args, index + 1, "--internal-name")?);
                index += 2;
            }
            "--window" => {
                let value = expect_string_value(&args, index + 1, "--window")?;
                window_mode = WindowMode::from_str(&value)?;
                index += 2;
            }
            "--uac" => {
                uac = true;
                index += 1;
            }
            "--quiet" => {
                quiet = true;
                index += 1;
            }
            "--verbose" => {
                verbose = true;
                index += 1;
            }
            value if value.starts_with("--") => {
                return Err(usage_error(format!("unknown option: {value}")));
            }
            _ => {
                if input_bat_path.is_none() {
                    input_bat_path = Some(PathBuf::from(args[index].clone()));
                    index += 1;
                } else {
                    return Err(usage_error("unexpected extra positional argument"));
                }
            }
        }
    }

    let verbosity = resolve_verbosity(quiet, verbose)?;
    let request = BuildRequest {
        input_bat_path: input_bat_path.ok_or_else(|| usage_error("missing input bat path"))?,
        output_exe_path,
        window_mode,
        uac,
        icon_path,
        version_info,
        overwrite: true,
        stub_paths: locate_stub_binaries(
            &env::current_exe().map_err(|error| usage_error(error.to_string()))?,
        )?,
    };
    let result = build_executable(&request)?;
    if !matches!(verbosity, Verbosity::Quiet) {
        print_json(&result)?;
    }
    Ok(0)
}

fn run_inspect(args: Vec<OsString>) -> Result<i32> {
    let mut executable_path: Option<PathBuf> = None;
    let mut quiet = false;
    let mut verbose = false;

    let mut index = 0;
    while index < args.len() {
        let key = args[index].to_string_lossy();
        match key.as_ref() {
            "--exe-path" => {
                executable_path = Some(expect_path_value(&args, index + 1, "--exe-path")?);
                index += 2;
            }
            "--quiet" => {
                quiet = true;
                index += 1;
            }
            "--verbose" => {
                verbose = true;
                index += 1;
            }
            value if value.starts_with("--") => {
                return Err(usage_error(format!("unknown option: {value}")));
            }
            _ => {
                if executable_path.is_none() {
                    executable_path = Some(PathBuf::from(args[index].clone()));
                    index += 1;
                } else {
                    return Err(usage_error("unexpected extra positional argument"));
                }
            }
        }
    }

    let verbosity = resolve_verbosity(quiet, verbose)?;
    let result = inspect_executable(
        &executable_path.ok_or_else(|| usage_error("missing executable path"))?,
    )?;
    if !matches!(verbosity, Verbosity::Quiet) {
        print_json(&result)?;
    }
    Ok(0)
}

fn run_verify(args: Vec<OsString>) -> Result<i32> {
    let mut script_path: Option<PathBuf> = None;
    let mut exe_path: Option<PathBuf> = None;
    let mut working_dir_path: Option<PathBuf> = None;
    let mut passthrough_args: Vec<OsString> = Vec::new();
    let mut quiet = false;
    let mut verbose = false;

    let mut index = 0;
    while index < args.len() {
        let key = args[index].to_string_lossy();
        match key.as_ref() {
            "--script-path" | "--script" => {
                script_path = Some(expect_path_value(&args, index + 1, &key)?);
                index += 2;
            }
            "--exe-path" | "--exe" => {
                exe_path = Some(expect_path_value(&args, index + 1, &key)?);
                index += 2;
            }
            "--cwd-path" | "--cwd" => {
                working_dir_path = Some(expect_path_value(&args, index + 1, &key)?);
                index += 2;
            }
            "--arg" => {
                passthrough_args.push(expect_os_value(&args, index + 1, "--arg")?);
                index += 2;
            }
            "--" => {
                passthrough_args.extend(args.into_iter().skip(index + 1));
                break;
            }
            "--quiet" => {
                quiet = true;
                index += 1;
            }
            "--verbose" => {
                verbose = true;
                index += 1;
            }
            value if value.starts_with("--") => {
                return Err(usage_error(format!("unknown option: {value}")));
            }
            _ => return Err(usage_error("verify only accepts named options")),
        }
    }

    let verbosity = resolve_verbosity(quiet, verbose)?;
    let result = verify(&VerifyRequest {
        script_path: script_path.ok_or_else(|| usage_error("missing --script-path"))?,
        exe_path: exe_path.ok_or_else(|| usage_error("missing --exe-path"))?,
        arguments: passthrough_args,
        working_dir: working_dir_path,
    })?;

    if !matches!(verbosity, Verbosity::Quiet) {
        print_json(&result)?;
    }

    Ok(if result.success { 0 } else { 1 })
}

fn print_json<T>(value: &T) -> Result<()>
where
    T: Serialize,
{
    let json = serde_json::to_string_pretty(value)
        .map_err(|error| usage_error(format!("failed to serialize result: {error}")))?;
    println!("{json}");
    Ok(())
}

fn expect_path_value(args: &[OsString], index: usize, option: &str) -> Result<PathBuf> {
    Ok(PathBuf::from(expect_os_value(args, index, option)?))
}

fn expect_string_value(args: &[OsString], index: usize, option: &str) -> Result<String> {
    Ok(expect_os_value(args, index, option)?
        .to_string_lossy()
        .to_string())
}

fn expect_os_value(args: &[OsString], index: usize, option: &str) -> Result<OsString> {
    args.get(index)
        .cloned()
        .ok_or_else(|| usage_error(format!("missing value for {option}")))
}

fn parse_version(args: &[OsString], index: usize, option: &str) -> Result<VersionTriplet> {
    let value = expect_string_value(args, index, option)?;
    VersionTriplet::from_str(&value)
}

fn resolve_verbosity(quiet: bool, verbose: bool) -> Result<Verbosity> {
    match (quiet, verbose) {
        (true, true) => Err(usage_error("--quiet and --verbose are mutually exclusive")),
        (true, false) => Ok(Verbosity::Quiet),
        (false, true) => Ok(Verbosity::Verbose),
        (false, false) => Ok(Verbosity::Normal),
    }
}

fn usage_error(message: impl Into<String>) -> Bat2PeError {
    Bat2PeError::new(ERR_CLI_USAGE, message)
}

fn print_usage() {
    println!(
        "\
bat2pe build --input-bat-path <input.bat|input.cmd> [--output-exe-path <output.exe>] [options]
bat2pe inspect --exe-path <output.exe> [--quiet|--verbose]
bat2pe verify --script-path <input.bat|input.cmd> --exe-path <output.exe> [--cwd-path PATH] [--arg VALUE ...]

Build options:
  --icon-path PATH
  --company TEXT
  --product TEXT
  --description TEXT
  --file-version X.Y.Z
  --product-version X.Y.Z
  --original-filename TEXT
  --internal-name TEXT
  --window visible|hidden
  --uac
  --quiet
  --verbose"
    );
}

enum Verbosity {
    Quiet,
    Normal,
    Verbose,
}
