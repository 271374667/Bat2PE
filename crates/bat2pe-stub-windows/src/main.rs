#![windows_subsystem = "windows"]

fn main() {
    let exit_code = match bat2pe_core::run_windows_stub() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };

    std::process::exit(exit_code);
}
