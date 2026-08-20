//! Talking to the Python CLI.
//!
//! Everything the TUI knows about configs and commands comes from
//! `uv run carcharoth ...` — never from a reimplementation of the loader.
//! Commands are spawned with an explicit argument vector (no shell), so
//! values containing spaces or quotes can never be reinterpreted.

use std::process::Command;

use anyhow::{anyhow, Result};
use serde_json::Value;

/// The program that owns the Python environment.
pub const LAUNCHER: &str = "uv";

/// `uv run carcharoth <args...>`: the full argv of a CLI invocation.
pub fn argv(args: &[String]) -> Vec<String> {
    let mut argv = vec!["run".to_string(), "carcharoth".to_string()];
    argv.extend(args.iter().cloned());
    argv
}

/// The command line as the user would type it — for review before running.
pub fn display_command(args: &[String]) -> String {
    let mut parts = vec![LAUNCHER.to_string()];
    parts.extend(argv(args));
    parts
        .iter()
        .map(|part| quote_for_display(part))
        .collect::<Vec<_>>()
        .join(" ")
}

fn quote_for_display(part: &str) -> String {
    if part.is_empty() || part.chars().any(|c| c.is_whitespace() || c == '"') {
        format!("'{}'", part)
    } else {
        part.to_string()
    }
}

/// A finished CLI invocation.
pub struct Output {
    pub ok: bool,
    pub stdout: String,
    pub stderr: String,
}

/// Run a CLI command to completion and capture its output. Used for the
/// short, read-only calls the TUI itself depends on (resolve/validate/
/// schema); long-running commands go through [`crate::runner`] instead.
pub fn capture(args: &[String]) -> Result<Output> {
    let output = Command::new(LAUNCHER)
        .args(argv(args))
        .output()
        .map_err(|err| anyhow!("failed to start `{}`: {err}", LAUNCHER))?;
    Ok(Output {
        ok: output.status.success(),
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    })
}

/// `--set path=value` arguments for a list of overrides, in stable order.
pub fn set_args(overrides: &[(String, String)]) -> Vec<String> {
    let mut args = Vec::new();
    for (path, value) in overrides {
        args.push("--set".to_string());
        args.push(format!("{path}={value}"));
    }
    args
}

/// The fully merged, validated config of a profile plus its content hash.
pub struct Resolved {
    pub config: Value,
    pub hash: String,
}

/// `config resolve -p <profile> --format json [--set ...]`.
pub fn resolve(profile: &str, overrides: &[(String, String)]) -> Result<Resolved> {
    let mut args = vec![
        "config".to_string(),
        "resolve".to_string(),
        "-p".to_string(),
        profile.to_string(),
        "--format".to_string(),
        "json".to_string(),
    ];
    args.extend(set_args(overrides));
    let output = capture(&args)?;
    if !output.ok {
        return Err(anyhow!(first_error_line(&output)));
    }
    let parsed: Value = serde_json::from_str(&output.stdout)
        .map_err(|err| anyhow!("could not parse `config resolve` output: {err}"))?;
    let config = parsed
        .get("config")
        .cloned()
        .ok_or_else(|| anyhow!("`config resolve` returned no 'config' key"))?;
    let hash = parsed
        .get("config_hash")
        .and_then(Value::as_str)
        .unwrap_or("?")
        .to_string();
    Ok(Resolved { config, hash })
}

/// The outcome of `config validate --json`: either valid, or a list of
/// `(path, message)` errors straight from pydantic / the layer loader.
pub enum Validation {
    Valid { hash: String },
    Invalid { errors: Vec<(String, String)> },
}

impl Validation {
    /// A single-line rendering of the failure, for status bars.
    pub fn summary(&self) -> String {
        match self {
            Validation::Valid { hash } => format!("valid (config_hash {hash})"),
            Validation::Invalid { errors } => errors
                .iter()
                .map(|(path, message)| {
                    if path.is_empty() {
                        message.clone()
                    } else {
                        format!("{path}: {message}")
                    }
                })
                .collect::<Vec<_>>()
                .join("; "),
        }
    }
}

/// `config validate -p <profile> --json [--set ...]`.
pub fn validate(profile: &str, overrides: &[(String, String)]) -> Result<Validation> {
    let mut args = vec![
        "config".to_string(),
        "validate".to_string(),
        "-p".to_string(),
        profile.to_string(),
        "--json".to_string(),
    ];
    args.extend(set_args(overrides));
    let output = capture(&args)?;
    parse_validation(&output)
}

fn parse_validation(output: &Output) -> Result<Validation> {
    // `validate --json` prints a JSON document on both exit paths; anything
    // else (a crash, a missing environment) is surfaced as an error.
    let parsed: Value = serde_json::from_str(output.stdout.trim())
        .map_err(|_| anyhow!(first_error_line(output)))?;
    if parsed.get("valid").and_then(Value::as_bool) == Some(true) {
        return Ok(Validation::Valid {
            hash: parsed
                .get("config_hash")
                .and_then(Value::as_str)
                .unwrap_or("?")
                .to_string(),
        });
    }
    let errors = parsed
        .get("errors")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| {
                    (
                        item.get("path")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        item.get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("invalid")
                            .to_string(),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Validation::Invalid { errors })
}

/// Whatever the CLI said about a failure, in one line.
fn first_error_line(output: &Output) -> String {
    for stream in [&output.stderr, &output.stdout] {
        if let Some(line) = stream.lines().map(str::trim).find(|l| !l.is_empty()) {
            return line.to_string();
        }
    }
    "command failed without output".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn argv_prefixes_the_launcher_subcommand() {
        assert_eq!(
            argv(&["backtest".to_string()]),
            vec!["run", "carcharoth", "backtest"]
        );
    }

    #[test]
    fn display_quotes_only_values_that_need_it() {
        let args = vec![
            "backtest".to_string(),
            "--set".to_string(),
            "symbols=[AAPL, MSFT]".to_string(),
        ];
        assert_eq!(
            display_command(&args),
            "uv run carcharoth backtest --set 'symbols=[AAPL, MSFT]'"
        );
    }

    #[test]
    fn set_args_keep_order_and_pair_each_override() {
        let overrides = vec![
            ("data.start".to_string(), "2025-01-01".to_string()),
            ("risk.max_open_positions".to_string(), "12".to_string()),
        ];
        assert_eq!(
            set_args(&overrides),
            vec![
                "--set",
                "data.start=2025-01-01",
                "--set",
                "risk.max_open_positions=12",
            ]
        );
    }

    #[test]
    fn validation_parses_structured_errors() {
        let output = Output {
            ok: false,
            stdout: r#"{"valid": false, "profile": "backtest",
                        "errors": [{"path": "risk.max_open_positions",
                                    "message": "must be > 0"}]}"#
                .to_string(),
            stderr: String::new(),
        };
        match parse_validation(&output).unwrap() {
            Validation::Invalid { errors } => {
                assert_eq!(
                    errors,
                    vec![(
                        "risk.max_open_positions".to_string(),
                        "must be > 0".to_string()
                    )]
                );
            }
            Validation::Valid { .. } => panic!("expected invalid"),
        }
    }

    #[test]
    fn validation_parses_success_hash() {
        let output = Output {
            ok: true,
            stdout: r#"{"valid": true, "profile": "backtest", "config_hash": "abc"}"#.to_string(),
            stderr: String::new(),
        };
        match parse_validation(&output).unwrap() {
            Validation::Valid { hash } => assert_eq!(hash, "abc"),
            Validation::Invalid { .. } => panic!("expected valid"),
        }
    }

    #[test]
    fn non_json_failure_becomes_an_error() {
        let output = Output {
            ok: false,
            stdout: String::new(),
            stderr: "\nno such profile\n".to_string(),
        };
        assert!(parse_validation(&output).is_err());
    }
}
