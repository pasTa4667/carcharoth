//! Talking to the Python CLI.
//!
//! Everything the TUI knows about configs and commands comes from
//! `uv run carcharoth ...` — never from a reimplementation of the loader.
//! Commands are spawned with an explicit argument vector (no shell), so
//! values containing spaces or quotes can never be reinterpreted.

use std::io::Read;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::{Duration, Instant};

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

/// A completed asynchronous JSON invocation.
pub struct JsonOutput {
    pub value: Value,
    pub stderr: String,
}

/// A cancellable, time-bounded JSON invocation.
pub struct JsonCommand {
    result: mpsc::Receiver<Result<JsonOutput>>,
    cancel: mpsc::Sender<()>,
    pid: Arc<AtomicU32>,
}

impl JsonCommand {
    pub fn start(args: &[String], timeout: Duration) -> Self {
        let mut command = Command::new(LAUNCHER);
        command.args(argv(args));
        Self::spawn(command, timeout)
    }

    fn spawn(mut command: Command, timeout: Duration) -> Self {
        let (result_tx, result) = mpsc::channel();
        let (cancel, cancel_rx) = mpsc::channel();
        let pid = Arc::new(AtomicU32::new(0));
        let worker_pid = Arc::clone(&pid);
        thread::spawn(move || {
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                command.process_group(0);
            }
            let outcome = (|| -> Result<JsonOutput> {
                let mut child = command
                    .stdin(Stdio::null())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .spawn()
                    .map_err(|err| anyhow!("failed to start `{LAUNCHER}`: {err}"))?;
                worker_pid.store(child.id(), Ordering::Release);
                let stdout = read_all(child.stdout.take().expect("stdout piped"));
                let stderr = read_all(child.stderr.take().expect("stderr piped"));
                let started = Instant::now();
                let ending = loop {
                    if cancel_rx.try_recv().is_ok() {
                        break "preview cancelled";
                    }
                    if started.elapsed() >= timeout {
                        break "preview timed out";
                    }
                    if let Some(status) = child.try_wait()? {
                        let stdout = stdout.join().unwrap_or_default();
                        let stderr = stderr.join().unwrap_or_default();
                        if !status.success() {
                            return Err(anyhow!(first_error(&stderr, &stdout)));
                        }
                        let stderr = String::from_utf8_lossy(&stderr).into_owned();
                        let value = serde_json::from_slice(&stdout).map_err(|err| {
                            let detail = stderr.lines().find(|line| !line.trim().is_empty());
                            match detail {
                                Some(line) => anyhow!("invalid CLI JSON: {err}: {}", line.trim()),
                                None => anyhow!("invalid CLI JSON: {err}"),
                            }
                        })?;
                        return Ok(JsonOutput { value, stderr });
                    }
                    thread::sleep(Duration::from_millis(25));
                };
                let _ = kill_process_tree(child.id());
                let _ = child.kill();
                let _ = child.wait();
                let stdout = stdout.join().unwrap_or_default();
                let stderr = stderr.join().unwrap_or_default();
                let detail = first_error(&stderr, &stdout);
                if detail == "command failed without output" {
                    Err(anyhow!(ending))
                } else {
                    Err(anyhow!("{ending}: {detail}"))
                }
            })();
            worker_pid.store(0, Ordering::Release);
            let _ = result_tx.send(outcome);
        });
        Self {
            result,
            cancel,
            pid,
        }
    }

    pub fn poll(&self) -> Option<Result<JsonOutput>> {
        match self.result.try_recv() {
            Ok(result) => Some(result),
            Err(mpsc::TryRecvError::Empty) => None,
            Err(mpsc::TryRecvError::Disconnected) => {
                Some(Err(anyhow!("preview worker stopped unexpectedly")))
            }
        }
    }

    pub fn cancel(&self) {
        let _ = self.cancel.send(());
        let pid = self.pid.load(Ordering::Acquire);
        if pid != 0 {
            let _ = kill_process_tree(pid);
        }
    }
}

impl Drop for JsonCommand {
    fn drop(&mut self) {
        self.cancel();
    }
}

fn read_all<R: Read + Send + 'static>(mut stream: R) -> thread::JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = stream.read_to_end(&mut bytes);
        bytes
    })
}

fn first_error(stderr: &[u8], stdout: &[u8]) -> String {
    for bytes in [stderr, stdout] {
        if let Some(line) = String::from_utf8_lossy(bytes)
            .lines()
            .rev()
            .map(str::trim)
            .find(|line| !line.is_empty())
        {
            return line.to_string();
        }
    }
    "command failed without output".to_string()
}

pub(crate) fn kill_process_tree(pid: u32) -> std::io::Result<()> {
    #[cfg(unix)]
    let status = Command::new("/bin/kill")
        .args(["-KILL", "--", &format!("-{pid}")])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    #[cfg(windows)]
    let status = Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()?;
    if status.success() {
        Ok(())
    } else {
        Err(std::io::Error::other("could not terminate process tree"))
    }
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

/// A short read-only JSON command, with normal CLI failures surfaced to the form.
pub fn capture_json(args: &[String]) -> Result<Value> {
    let output = capture(args)?;
    if !output.ok {
        return Err(anyhow!(first_error_line(&output)));
    }
    serde_json::from_str(&output.stdout).map_err(|err| anyhow!("invalid CLI JSON: {err}"))
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
        if let Some(line) = stream.lines().rev().map(str::trim).find(|l| !l.is_empty()) {
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

    #[test]
    fn cli_errors_prefer_the_final_message_over_usage_text() {
        let output = Output {
            ok: false,
            stdout: String::new(),
            stderr: "usage: carcharoth backtest-loop ...\nerror: invalid date\n".into(),
        };
        assert_eq!(first_error_line(&output), "error: invalid date");
        assert_eq!(
            first_error(output.stderr.as_bytes(), output.stdout.as_bytes()),
            "error: invalid date"
        );
    }

    #[cfg(unix)]
    #[test]
    fn async_json_command_times_out_and_reports_stderr() {
        let mut command = Command::new("sh");
        command.args(["-c", "echo still-working >&2; sleep 5"]);
        let job = JsonCommand::spawn(command, Duration::from_millis(50));
        let deadline = Instant::now() + Duration::from_secs(2);
        let error = loop {
            if let Some(result) = job.poll() {
                break result.err().expect("command should time out").to_string();
            }
            assert!(Instant::now() < deadline, "preview worker did not finish");
            thread::sleep(Duration::from_millis(10));
        };
        assert!(error.contains("preview timed out"));
        assert!(error.contains("still-working"));
    }
}
