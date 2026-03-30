fn main() {
    let exit_code = match bat2pe_core::maybe_run_current_executable() {
        Ok(Some(code)) => code,
        Ok(None) => {
            eprintln!("bat2pe runtime host is missing an embedded payload");
            1
        }
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };

    std::process::exit(exit_code);
}
