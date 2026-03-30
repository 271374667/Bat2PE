mod build;
mod error;
mod inspect;
mod model;
mod overlay;
mod resources;
mod runtime;
mod verify;

pub use build::{
    build_executable, derive_output_exe_path, detect_script_encoding, locate_template_executable,
    read_script_bytes,
};
pub use error::{
    Bat2PeError, ERR_CLI_USAGE, ERR_DIRECTORY_NOT_WRITABLE, ERR_INVALID_EXECUTABLE,
    ERR_INVALID_INPUT, ERR_IO, ERR_RESOURCE_NOT_FOUND, ERR_UNSUPPORTED_ENCODING,
    ERR_UNSUPPORTED_INPUT, ERR_VERIFY_MISMATCH, ERR_VERIFY_UAC_INTERACTIVE, Result,
};
pub use inspect::inspect_executable;
pub use model::{
    BuildRequest, BuildResult, EmbeddedMetadata, IconInfo, InspectResult, RuntimeConfig,
    ScriptEncoding, VerifyExecution, VerifyRequest, VerifyResult, VersionInfo, VersionTriplet,
    WindowMode,
};
pub use runtime::{maybe_run_current_executable, run_console_stub, run_windows_stub};
pub use verify::verify;
