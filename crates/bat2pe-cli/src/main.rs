use std::env;
use std::ffi::OsString;
use std::path::PathBuf;
use std::process;
use std::str::FromStr;

use bat2pe_core::{
    Bat2PeError, BuildRequest, ERR_CLI_USAGE, Result, VersionInfo, VersionTriplet, WindowMode,
    build_executable, locate_template_executable, maybe_run_current_executable,
};
use serde::Serialize;

const CLI_VERSION: &str = env!("CARGO_PKG_VERSION");

fn main() {
    let exit_code = match dispatch_main() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };

    process::exit(exit_code);
}

fn dispatch_main() -> Result<i32> {
    if let Some(exit_code) = maybe_run_current_executable()? {
        return Ok(exit_code);
    }

    real_main()
}

fn real_main() -> Result<i32> {
    let mut args: Vec<OsString> = env::args_os().skip(1).collect();
    if args.is_empty() {
        print_root_help();
        return Err(usage_error("missing subcommand"));
    }

    let command = args.remove(0);
    match command.to_string_lossy().as_ref() {
        "build" => run_build(args),
        "inspect" | "verify" => Err(usage_error(
            "'inspect' and 'verify' are not available as user commands",
        )),
        "help" => run_help(args),
        "version" | "-V" | "--version" => {
            print_version();
            Ok(0)
        }
        "-h" | "--help" => {
            print_root_help();
            Ok(0)
        }
        _ => {
            // Drag-and-drop: unknown first token is treated as the input bat path.
            args.insert(0, command);
            run_build(args)
        }
    }
}

fn run_help(args: Vec<OsString>) -> Result<i32> {
    match args.as_slice() {
        [] => {
            print_root_help();
            Ok(0)
        }
        [topic] => match topic.to_string_lossy().as_ref() {
            "build" => {
                print_build_help();
                Ok(0)
            }
            "version" => {
                print_version();
                Ok(0)
            }
            "-h" | "--help" => {
                print_root_help();
                Ok(0)
            }
            other => Err(usage_error(format!("unknown help topic: {other}"))),
        },
        _ => Err(usage_error("help accepts at most one command name")),
    }
}

fn run_build(args: Vec<OsString>) -> Result<i32> {
    let mut input_bat_path: Option<PathBuf> = None;
    let mut output_exe_path: Option<PathBuf> = None;
    let mut icon_path: Option<PathBuf> = None;
    let mut version_info = VersionInfo::default();
    let mut window_mode = WindowMode::Hidden;
    let mut uac = false;
    let mut quiet = false;

    let mut index = 0;
    while index < args.len() {
        let key = args[index].to_string_lossy();
        match key.as_ref() {
            "-h" | "--help" => {
                print_build_help();
                return Ok(0);
            }
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
            "--visible" => {
                window_mode = WindowMode::Visible;
                index += 1;
            }
            "--uac" => {
                uac = true;
                index += 1;
            }
            "--quiet" => {
                quiet = true;
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

    let current_exe = env::current_exe().map_err(|error| usage_error(error.to_string()))?;
    let request = BuildRequest {
        input_bat_path: input_bat_path.ok_or_else(|| usage_error("missing input bat path"))?,
        output_exe_path,
        template_executable: bat2pe_core::TemplateExecutable::Path(locate_template_executable(
            &current_exe,
        )?),
        window_mode,
        uac,
        icon_path,
        version_info,
        overwrite: true,
    };
    let result = build_executable(&request)?;
    if !quiet {
        print_json(&result)?;
    }
    Ok(0)
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

fn usage_error(message: impl Into<String>) -> Bat2PeError {
    Bat2PeError::new(ERR_CLI_USAGE, message)
}

fn print_version() {
    println!("bat2pe {CLI_VERSION}");
}

fn print_root_help() {
    println!("{}", root_help_text());
}

fn print_build_help() {
    println!("{}", build_help_text());
}

fn root_help_text() -> &'static str {
    r#"Bat2PE CLI

Convert .bat/.cmd scripts into standalone .exe files.

Drop a .bat or .cmd file onto Bat2PE.exe to convert it instantly, or use the
build command for full control over output options.

Usage:
  bat2pe <INPUT_BAT_PATH>
  bat2pe build <INPUT_BAT_PATH> [OPTIONS]
  bat2pe help [COMMAND]

Commands:
  build      Convert a .bat or .cmd script into an executable.
  help       Show the root help or the help for a specific command.
  version    Show the bat2pe CLI version.

Options:
  -h, --help  Show this help.
  -V, --version  Show the bat2pe CLI version.

Examples:
  bat2pe run.bat
  bat2pe build run.bat
  bat2pe build run.cmd --output-exe-path dist\run.exe --visible
  bat2pe help build

Use "bat2pe build --help" for detailed build options."#
}

fn build_help_text() -> &'static str {
    r#"Build Command

Convert one .bat/.cmd script into a standalone Windows executable.

Usage:
  bat2pe build <INPUT_BAT_PATH> [OPTIONS]
  bat2pe build --input-bat-path <INPUT_BAT_PATH> [OPTIONS]

Arguments:
  <INPUT_BAT_PATH>
      Input batch script. This positional form is equivalent to --input-bat-path.

Options:
  --input-bat-path PATH
      Explicit input script path. Must end with .bat or .cmd.
  --output-exe-path, --out PATH
      Output executable path. If omitted, bat2pe writes <script-stem>.exe beside
      the input script and overwrites any existing file at that path.
  --icon-path, --icon PATH
      Optional .ico file to embed into the generated executable.
  --company TEXT
      CompanyName field written into the Windows version resource.
  --product TEXT
      ProductName field written into the Windows version resource.
  --description TEXT
      FileDescription field written into the Windows version resource.
  --file-version X.Y.Z
      FileVersion triplet written into the Windows version resource.
  --product-version X.Y.Z
      ProductVersion triplet written into the Windows version resource.
  --original-filename TEXT
      OriginalFilename field stored in metadata and the Windows version resource.
      Defaults to the generated output file name when omitted.
  --internal-name TEXT
      InternalName field stored in metadata and the Windows version resource.
      Defaults to the generated output file stem when omitted.
  --visible
      Show a console window when the generated executable is launched.
      Without this flag, the executable hides the console window (GUI subsystem).
  --uac
      Write a requireAdministrator execution level into the generated manifest.
      Without this flag, the executable uses asInvoker.
  --quiet
      Suppress the JSON success output.
  -h, --help
      Show this help.

Examples:
  bat2pe build run.bat
  bat2pe build --input-bat-path run.cmd --output-exe-path dist\run.exe
  bat2pe build run.bat --icon-path app.ico --company Acme --product Runner
  bat2pe build run.bat --visible
  bat2pe build admin.cmd --uac"#
}
