use std::fmt::{self, Display, Formatter};
use std::path::{Path, PathBuf};

use serde::Serialize;

pub const ERR_INVALID_INPUT: u32 = 100;
pub const ERR_UNSUPPORTED_INPUT: u32 = 101;
pub const ERR_UNSUPPORTED_ENCODING: u32 = 102;
pub const ERR_RESOURCE_NOT_FOUND: u32 = 103;
pub const ERR_INVALID_EXECUTABLE: u32 = 104;
pub const ERR_DIRECTORY_NOT_WRITABLE: u32 = 105;
pub const ERR_IO: u32 = 106;
pub const ERR_CLI_USAGE: u32 = 107;
pub const ERR_VERIFY_MISMATCH: u32 = 108;

#[derive(Debug, Clone, Serialize)]
pub struct Bat2PeError {
    pub code: u32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<PathBuf>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<String>,
}

impl Bat2PeError {
    pub fn new(code: u32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            path: None,
            details: None,
        }
    }

    pub fn with_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.path = Some(path.into());
        self
    }

    pub fn with_details(mut self, details: impl Into<String>) -> Self {
        self.details = Some(details.into());
        self
    }

    pub fn io(path: &Path, error: &std::io::Error) -> Self {
        Self::new(ERR_IO, error.to_string())
            .with_path(path.to_path_buf())
            .with_details(format!("io error kind: {:?}", error.kind()))
    }

    pub fn to_json_string(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| {
            format!(
                r#"{{"code":{},"message":"{}"}}"#,
                self.code,
                self.message.replace('"', "\\\"")
            )
        })
    }
}

impl Display for Bat2PeError {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.code, self.message)?;
        if let Some(path) = &self.path {
            write!(f, " ({})", path.display())?;
        }
        if let Some(details) = &self.details {
            write!(f, ": {details}")?;
        }
        Ok(())
    }
}

impl std::error::Error for Bat2PeError {}

pub type Result<T> = std::result::Result<T, Bat2PeError>;
