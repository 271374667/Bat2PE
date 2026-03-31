use std::ffi::OsString;
use std::fmt::{self, Display, Formatter};
use std::path::Path;
use std::path::PathBuf;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

use crate::error::{Bat2PeError, ERR_INVALID_INPUT};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum WindowMode {
    Visible,
    #[default]
    Hidden,
}

impl FromStr for WindowMode {
    type Err = Bat2PeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "visible" => Ok(Self::Visible),
            "hidden" => Ok(Self::Hidden),
            other => Err(Bat2PeError::new(
                ERR_INVALID_INPUT,
                format!("unsupported window mode: {other}"),
            )),
        }
    }
}

impl Display for WindowMode {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Visible => write!(f, "visible"),
            Self::Hidden => write!(f, "hidden"),
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ScriptEncoding {
    Utf8,
    Utf8Bom,
    Utf16LeBom,
    AnsiGbk,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct VersionTriplet {
    pub major: u16,
    pub minor: u16,
    pub patch: u16,
}

impl FromStr for VersionTriplet {
    type Err = Bat2PeError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let mut parts = value.split('.');
        let major = parts
            .next()
            .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "missing major version"))?
            .parse::<u16>()
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "invalid major version"))?;
        let minor = parts
            .next()
            .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "missing minor version"))?
            .parse::<u16>()
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "invalid minor version"))?;
        let patch = parts
            .next()
            .ok_or_else(|| Bat2PeError::new(ERR_INVALID_INPUT, "missing patch version"))?
            .parse::<u16>()
            .map_err(|_| Bat2PeError::new(ERR_INVALID_INPUT, "invalid patch version"))?;

        if parts.next().is_some() {
            return Err(Bat2PeError::new(
                ERR_INVALID_INPUT,
                "version must use major.minor.patch",
            ));
        }

        Ok(Self {
            major,
            minor,
            patch,
        })
    }
}

impl Display for VersionTriplet {
    fn fmt(&self, f: &mut Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}.{}", self.major, self.minor, self.patch)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct VersionInfo {
    pub company_name: Option<String>,
    pub product_name: Option<String>,
    pub file_description: Option<String>,
    pub file_version: Option<VersionTriplet>,
    pub product_version: Option<VersionTriplet>,
    pub original_filename: Option<String>,
    pub internal_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct IconInfo {
    pub file_name: String,
    pub source_path: String,
    pub size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RuntimeConfig {
    pub window_mode: WindowMode,
    pub temp_script_suffix: String,
    pub strict_dp0: bool,
    #[serde(default)]
    pub uac: bool,
}

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            window_mode: WindowMode::Hidden,
            temp_script_suffix: ".cmd".to_string(),
            strict_dp0: true,
            uac: false,
        }
    }
}

#[derive(Debug, Clone)]
pub enum TemplateExecutable {
    Path(PathBuf),
    Embedded {
        logical_path: PathBuf,
        bytes: &'static [u8],
    },
}

impl TemplateExecutable {
    pub fn logical_path(&self) -> &Path {
        match self {
            Self::Path(path) => path.as_path(),
            Self::Embedded { logical_path, .. } => logical_path.as_path(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BuildRequest {
    pub input_bat_path: PathBuf,
    pub output_exe_path: Option<PathBuf>,
    pub template_executable: TemplateExecutable,
    pub window_mode: WindowMode,
    pub uac: bool,
    pub icon_path: Option<PathBuf>,
    pub version_info: VersionInfo,
    pub overwrite: bool,
}

#[derive(Debug, Clone)]
pub struct VerifyRequest {
    pub script_path: PathBuf,
    pub exe_path: PathBuf,
    pub arguments: Vec<OsString>,
    pub working_dir: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EmbeddedMetadata {
    pub schema_version: u32,
    pub source_script_name: String,
    pub source_extension: String,
    pub script_encoding: ScriptEncoding,
    pub script_length: u64,
    pub runtime: RuntimeConfig,
    pub icon: Option<IconInfo>,
    pub version_info: VersionInfo,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InspectResult {
    pub exe_path: PathBuf,
    pub source_script_name: String,
    pub source_extension: String,
    pub script_encoding: ScriptEncoding,
    pub script_length: u64,
    pub runtime: RuntimeConfig,
    pub icon: Option<IconInfo>,
    pub version_info: VersionInfo,
    pub schema_version: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildResult {
    pub output_exe_path: PathBuf,
    #[serde(alias = "stub_path")]
    pub template_executable_path: PathBuf,
    pub script_encoding: ScriptEncoding,
    pub script_length: u64,
    pub window_mode: WindowMode,
    #[serde(default)]
    pub uac: bool,
    pub inspect: InspectResult,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerifyExecution {
    pub exit_code: i32,
    pub stderr: String,
    pub stdout: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerifyResult {
    pub script: VerifyExecution,
    pub executable: VerifyExecution,
    pub exit_code_match: bool,
    pub stderr_match: bool,
    pub success: bool,
}

#[cfg(test)]
mod tests {
    use std::str::FromStr;

    use super::{VersionTriplet, WindowMode};
    use crate::error::ERR_INVALID_INPUT;

    #[test]
    fn parses_version_triplet() {
        let version = VersionTriplet::from_str("1.2.3").expect("valid version");
        assert_eq!(version.major, 1);
        assert_eq!(version.minor, 2);
        assert_eq!(version.patch, 3);
        assert_eq!(version.to_string(), "1.2.3");
    }

    #[test]
    fn rejects_invalid_version_triplet() {
        let error = VersionTriplet::from_str("1.2.3.4").expect_err("invalid version");
        assert_eq!(error.code, ERR_INVALID_INPUT);
        assert!(error.message.contains("major.minor.patch"));
    }

    #[test]
    fn rejects_invalid_window_mode() {
        let error = WindowMode::from_str("fullscreen").expect_err("invalid window mode");
        assert_eq!(error.code, ERR_INVALID_INPUT);
        assert!(error.message.contains("unsupported window mode"));
    }
}
