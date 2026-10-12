//! A dedicated loop form. Python owns dates, snapshot validation, and execution.
use anyhow::{anyhow, Result};
use crossterm::event::KeyCode;
use serde_json::Value;
use std::time::Duration;

use crate::catalog::{resolve_model_artifact_options, Field, Kind, OMIT};
use crate::cli;

const PERIODS: &[&str] = &["quarterly", "half-year", "yearly"];
const PREVIEW_TIMEOUT: Duration = Duration::from_secs(30);
pub const FIELDS: &[Field] = &[
    Field::text(
        "profile",
        "profile",
        "-p",
        "one profile for training and backtests",
    ),
    Field::text(
        "iterations",
        "iterations",
        "--iterations",
        "positive number of independent backtests",
    ),
    Field::text(
        "label",
        "loop label",
        "--label",
        "optional readable Grafana label",
    ),
    Field::flag(
        "--train",
        "train each iteration",
        "fresh training on an expanding window",
    ),
    Field::text(
        "train-start",
        "train start",
        "--train-start",
        "YYYY-MM-DD; stays fixed across iterations",
    ),
    Field::choice(
        "train-period",
        "initial training period",
        "--train-period",
        PERIODS,
        "training expands by one backtest period each iteration",
    ),
    Field::choice(
        "validation-period",
        "validation period",
        "--validation-period",
        PERIODS,
        "starts immediately after training",
    ),
    Field::choice(
        "artifact",
        "model artifact",
        "--artifact",
        &[],
        "required when training is off; F2 refreshes local artifacts",
    ),
    Field::text(
        "start",
        "backtest start",
        "--start",
        "YYYY-MM-DD; strictly after artifact validation",
    ),
    Field::choice(
        "backtest-period",
        "backtest period",
        "--backtest-period",
        PERIODS,
        "fresh capital and positions for every period",
    ),
    Field::text(
        "root",
        "universe root",
        "",
        "F2 loads snapshots from this directory",
    ),
    Field::choice(
        "snapshot",
        "universe artifact",
        "--snapshot",
        &[],
        "select a frozen universe first; F2 refreshes",
    ),
    Field::flag(
        "--all-buckets",
        "all buckets",
        "run every published bucket sequentially, including the combined all bucket",
    ),
    Field::choice(
        "bucket",
        "universe bucket",
        "--bucket",
        &[],
        "all or a published bucket; diagnostic clusters are excluded",
    ),
    Field::choice(
        "prepared-history",
        "prepared history",
        "--prepared-history",
        &["false", "true"],
        "opt-in: batch initialization can differ from rolling live history",
    ),
    Field::flag(
        "--no-hmm",
        "disable HMM",
        "train and replay without HMM regime features",
    ),
    Field::flag("--verbose", "verbose", "INFO console logging"),
];

pub struct LoopForm {
    pub values: Vec<String>,
    pub cursor: usize,
    pub loaded: bool,
    pub preview: Option<Value>,
    pub preview_error: Option<String>,
    pub preview_scroll: usize,
    preview_args: Vec<String>,
    pending_preview_args: Vec<String>,
    preview_job: Option<cli::JsonCommand>,
    options: Vec<Vec<String>>,
    snapshots: Vec<Value>,
}

impl Default for LoopForm {
    fn default() -> Self {
        let mut form = Self {
            values: FIELDS.iter().map(Field::initial).collect(),
            cursor: 0,
            loaded: false,
            preview: None,
            preview_error: None,
            preview_scroll: 0,
            preview_args: vec![],
            pending_preview_args: vec![],
            preview_job: None,
            options: vec![vec![]; FIELDS.len()],
            snapshots: vec![],
        };
        for (key, value) in [
            ("profile", "backtest"),
            ("iterations", "1"),
            ("train-period", "yearly"),
            ("root", "artifacts/universes"),
        ] {
            form.set(key, value.to_string());
        }
        form
    }
}

fn index(key: &str) -> usize {
    FIELDS
        .iter()
        .position(|f| f.key == key)
        .expect("known loop field")
}

impl LoopForm {
    pub fn value(&self, key: &str) -> &str {
        &self.values[index(key)]
    }
    fn set(&mut self, key: &str, value: String) {
        self.values[index(key)] = value;
    }
    pub fn training(&self) -> bool {
        self.value("--train") == "on"
    }
    pub fn all_buckets(&self) -> bool {
        self.value("--all-buckets") == "on"
    }

    pub fn visible(&self) -> Vec<usize> {
        FIELDS
            .iter()
            .enumerate()
            .filter_map(|(i, f)| {
                let visible = match f.key {
                    "train-start" | "train-period" | "validation-period" => self.training(),
                    "artifact" | "start" => !self.training(),
                    "bucket" => !self.all_buckets(),
                    _ => true,
                };
                visible.then_some(i)
            })
            .collect()
    }

    pub fn invalidate(&mut self) {
        self.cancel_preview();
        self.preview = None;
        self.preview_error = None;
        self.preview_args.clear();
        self.preview_scroll = 0;
    }

    pub fn previewing(&self) -> bool {
        self.preview_job.is_some()
    }

    pub fn cancel_preview(&mut self) -> bool {
        let Some(job) = self.preview_job.take() else {
            return false;
        };
        drop(job);
        self.pending_preview_args.clear();
        true
    }

    pub fn args(&self, overrides: &[(String, String)]) -> Vec<String> {
        let mut args = vec!["backtest-loop".to_string()];
        args.extend(cli::set_args(overrides));
        for i in self.visible() {
            let f = &FIELDS[i];
            let value = self.values[i].trim();
            if f.flag.is_empty() || value.is_empty() {
                continue;
            }
            if f.kind == Kind::Flag {
                if value == "on" {
                    args.push(f.flag.to_string());
                }
            } else {
                args.extend([f.flag.to_string(), value.to_string()]);
            }
        }
        args
    }

    fn check_required(&self) -> Result<()> {
        let mut required = vec!["profile", "iterations", "snapshot"];
        if !self.all_buckets() {
            required.push("bucket");
        }
        if self.training() {
            required.push("train-start");
        } else {
            required.extend(["artifact", "start"]);
        }
        for key in required {
            if self.value(key).trim().is_empty() {
                return Err(anyhow!(
                    "{} is required (F2 refreshes artifacts)",
                    FIELDS[index(key)].label
                ));
            }
        }
        if self
            .value("iterations")
            .parse::<usize>()
            .ok()
            .filter(|n| *n > 0)
            .is_none()
        {
            return Err(anyhow!("iterations must be a positive integer"));
        }
        Ok(())
    }

    pub fn refresh(&mut self, overrides: &[(String, String)]) -> Result<String> {
        let result = self.try_refresh(overrides);
        if result.is_err() {
            for key in ["snapshot", "bucket", "artifact"] {
                self.set(key, String::new());
                self.options[index(key)].clear();
            }
        }
        result
    }

    fn try_refresh(&mut self, overrides: &[(String, String)]) -> Result<String> {
        self.loaded = true;
        self.invalidate();
        // Clear first: failed refreshes must not leave stale dependent selections runnable.
        self.snapshots.clear();
        self.options[index("snapshot")].clear();
        self.options[index("bucket")].clear();
        let catalogue = cli::capture_json(&[
            "universe".into(),
            "list".into(),
            "--root".into(),
            self.value("root").into(),
            "--json".into(),
        ])?;
        self.load_snapshots(&catalogue);
        let resolved = cli::resolve(self.value("profile"), overrides)?;
        self.options[index("artifact")] = resolve_model_artifact_options(&resolved.config)
            .into_iter()
            .filter(|p| p != OMIT)
            .filter_map(|p| p.strip_prefix("model.artifact_path=").map(str::to_string))
            .collect();
        self.keep_valid("artifact");
        let warnings = catalogue["warnings"]
            .as_array()
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join("; ")
            })
            .unwrap_or_default();
        Ok(if warnings.is_empty() {
            "refreshed local artifacts; Enter previews the schedule".into()
        } else {
            warnings
        })
    }

    fn keep_valid(&mut self, key: &str) {
        let i = index(key);
        if !self.options[i].contains(&self.values[i]) {
            self.values[i].clear();
        }
    }

    fn load_snapshots(&mut self, catalogue: &Value) {
        self.snapshots = catalogue["snapshots"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        self.options[index("snapshot")] = self
            .snapshots
            .iter()
            .filter_map(|s| s["path"].as_str().map(str::to_string))
            .collect();
        self.keep_valid("snapshot");
        self.load_buckets();
    }

    fn load_buckets(&mut self) {
        self.options[index("bucket")] = self
            .snapshots
            .iter()
            .find(|s| s["path"].as_str() == Some(self.value("snapshot")))
            .and_then(|s| s["buckets"].as_object())
            .map(|b| b.keys().cloned().collect())
            .unwrap_or_default();
        self.keep_valid("bucket");
        self.prefill_train_start();
    }

    fn prefill_train_start(&mut self) {
        if !self.training() || !self.value("train-start").is_empty() {
            return;
        }
        let start = self
            .snapshots
            .iter()
            .find(|snapshot| snapshot["path"].as_str() == Some(self.value("snapshot")))
            .and_then(|snapshot| snapshot["earliest_training_start"].as_str())
            .map(str::to_string);
        if let Some(start) = start {
            self.set("train-start", start);
        }
    }

    pub fn universe_hint(&self) -> String {
        self.snapshots
            .iter()
            .find(|s| s["path"].as_str() == Some(self.value("snapshot")))
            .map(|s| {
                let selection = if self.all_buckets() {
                    format!(
                        "{} buckets",
                        s["buckets"].as_object().map(|b| b.len()).unwrap_or(0)
                    )
                } else {
                    format!("{} members", s["buckets"][self.value("bucket")])
                };
                format!(
                    "discovery ends {} · {selection} · survivorship-biased",
                    s["discovery_end"].as_str().unwrap_or("?")
                )
            })
            .unwrap_or_else(|| "select a universe artifact (F2 refresh)".into())
    }

    pub fn request_preview(&mut self, overrides: &[(String, String)]) -> Result<()> {
        self.invalidate();
        self.check_required()?;
        let args = self.args(overrides);
        let mut preview_args = args.clone();
        preview_args.extend(["--dry-run".into(), "--json".into()]);
        self.preview_job = Some(cli::JsonCommand::start(&preview_args, PREVIEW_TIMEOUT));
        self.pending_preview_args = args;
        Ok(())
    }

    pub fn poll_preview(&mut self) -> Option<Result<String>> {
        let result = self.preview_job.as_ref()?.poll()?;
        self.preview_job.take();
        let args = std::mem::take(&mut self.pending_preview_args);
        Some(match result {
            Ok(output) => {
                self.preview = Some(output.value);
                self.preview_args = args;
                let warning = output
                    .stderr
                    .lines()
                    .map(str::trim)
                    .find(|line| !line.is_empty());
                Ok(match warning {
                    Some(line) => format!("schedule validated; CLI warning: {line}"),
                    None => "schedule validated; review it, then press Enter again".into(),
                })
            }
            Err(err) => {
                let message = err.to_string();
                self.preview_error = Some(message.clone());
                Err(anyhow!(message))
            }
        })
    }

    pub fn ready_args(&self, overrides: &[(String, String)]) -> Result<Vec<String>> {
        self.check_required()?;
        let args = self.args(overrides);
        if self.preview.is_none() || args != self.preview_args {
            return Err(anyhow!(
                "Enter previews and validates the current settings before Start"
            ));
        }
        Ok(args)
    }

    pub fn invalidate_if_changed(&mut self, overrides: &[(String, String)]) {
        let args = self.args(overrides);
        if (self.preview.is_some() && args != self.preview_args)
            || (self.previewing() && args != self.pending_preview_args)
        {
            self.invalidate();
        }
    }

    pub fn edit(&mut self, code: KeyCode) {
        let visible = self.visible();
        match code {
            KeyCode::Up => {
                self.cursor = self.cursor.saturating_sub(1);
                return;
            }
            KeyCode::Down => {
                self.cursor = (self.cursor + 1).min(visible.len() - 1);
                return;
            }
            KeyCode::PageUp => {
                self.preview_scroll = self.preview_scroll.saturating_sub(1);
                return;
            }
            KeyCode::PageDown => {
                let count = self
                    .preview
                    .as_ref()
                    .and_then(|v| v["windows"].as_array())
                    .map(Vec::len)
                    .unwrap_or(0);
                self.preview_scroll = (self.preview_scroll + 1).min(count.saturating_sub(1));
                return;
            }
            _ => {}
        }
        let i = visible[self.cursor];
        let before = self.values[i].clone();
        match (FIELDS[i].kind, code) {
            (Kind::Text, KeyCode::Char(c)) => self.values[i].push(c),
            (Kind::Text, KeyCode::Backspace) => {
                self.values[i].pop();
            }
            (kind, KeyCode::Left | KeyCode::Right | KeyCode::Char(' ')) if kind != Kind::Text => {
                let options: Vec<String> = match kind {
                    Kind::Flag => vec!["off".into(), "on".into()],
                    Kind::Choice(items) if !items.is_empty() => {
                        items.iter().map(|s| s.to_string()).collect()
                    }
                    _ => self.options[i].clone(),
                };
                if options.is_empty() {
                    return;
                }
                let next = match options.iter().position(|s| s == &before) {
                    None => 0,
                    Some(current) if code == KeyCode::Left => {
                        (current + options.len() - 1) % options.len()
                    }
                    Some(current) => (current + 1) % options.len(),
                };
                self.values[i] = options[next].clone();
            }
            _ => {}
        }
        if before != self.values[i] {
            self.invalidate();
            match FIELDS[i].key {
                "snapshot" => {
                    self.set("bucket", String::new());
                    self.load_buckets();
                }
                "--train" => self.prefill_train_start(),
                "root" => {
                    self.set("snapshot", String::new());
                    self.set("bucket", String::new());
                    self.options[index("snapshot")].clear();
                    self.options[index("bucket")].clear();
                    self.snapshots.clear();
                }
                "profile" => {
                    self.set("artifact", String::new());
                    self.options[index("artifact")].clear();
                }
                _ => {}
            }
            self.cursor = self.cursor.min(self.visible().len() - 1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn conditional_fields_args_and_stale_preview() {
        let mut form = LoopForm::default();
        assert!(!form.visible().contains(&index("train-start")));
        assert!(form.visible().contains(&index("artifact")));
        assert!(form.ready_args(&[]).is_err());
        for (key, value) in [
            ("--train", "on"),
            ("train-start", "2020-01-01"),
            ("snapshot", "universe/buckets.yaml"),
            ("bucket", "all"),
        ] {
            form.set(key, value.into());
        }
        assert!(!form.visible().contains(&index("artifact")));
        let args = form.args(&[]);
        assert!(args
            .windows(2)
            .any(|v| v == ["--prepared-history", "false"]));
        assert!(args.windows(2).any(|v| v == ["--train-period", "yearly"]));
        assert!(!args.contains(&"--artifact".to_string()));
        assert!(!args.contains(&"--no-hmm".to_string()));
        form.set("--no-hmm", "on".into());
        assert!(form.args(&[]).contains(&"--no-hmm".to_string()));
        form.set("--no-hmm", "off".into());
        form.preview = Some(json!({"windows": []}));
        form.preview_args = args.clone();
        assert_eq!(form.ready_args(&[]).unwrap(), args);
        let overrides = vec![("risk.max_open_positions".into(), "4".into())];
        assert!(form.ready_args(&overrides).is_err());
        form.invalidate_if_changed(&overrides);
        assert!(form.preview.is_none());
        form.set("iterations", "0".into());
        assert!(form.check_required().is_err());
    }

    #[test]
    fn all_buckets_toggle_hides_selection_and_invalidates_preview() {
        let mut form = LoopForm::default();
        for (key, value) in [
            ("--train", "on"),
            ("train-start", "2020-01-01"),
            ("snapshot", "a"),
        ] {
            form.set(key, value.into());
        }
        form.load_snapshots(&json!({"snapshots": [{
            "path": "a", "buckets": {"all": 10, "bucket-001": 4}
        }]}));
        assert!(!form.all_buckets());
        assert!(form.check_required().is_err());
        form.cursor = form
            .visible()
            .iter()
            .position(|i| *i == index("--all-buckets"))
            .unwrap();
        form.edit(KeyCode::Char(' '));
        assert!(form.all_buckets());
        assert!(!form.visible().contains(&index("bucket")));
        assert!(form.check_required().is_ok());
        let args = form.args(&[]);
        assert!(args.contains(&"--all-buckets".into()));
        assert!(!args.contains(&"--bucket".into()));
        assert!(form.universe_hint().contains("2 buckets"));
        form.preview = Some(json!({"windows": []}));
        form.preview_args = args.clone();
        assert_eq!(form.ready_args(&[]).unwrap(), args);
        form.edit(KeyCode::Right);
        assert!(!form.all_buckets());
        assert!(form.preview.is_none());
        assert!(form.visible().contains(&index("bucket")));
        assert!(form.check_required().is_err());
        form.cursor = form
            .visible()
            .iter()
            .position(|i| *i == index("bucket"))
            .unwrap();
        form.edit(KeyCode::Right);
        assert_eq!(form.value("bucket"), "all");
        form.edit(KeyCode::Right);
        assert_eq!(form.value("bucket"), "bucket-001");
        assert!(form.check_required().is_ok());
        assert!(form
            .args(&[])
            .windows(2)
            .any(|v| v == ["--bucket", "bucket-001"]));
        form.cursor = form
            .visible()
            .iter()
            .position(|i| *i == index("--all-buckets"))
            .unwrap();
        form.edit(KeyCode::Right);
        form.edit(KeyCode::Left);
        assert_eq!(form.value("bucket"), "bucket-001");
    }

    #[test]
    fn training_start_defaults_to_the_day_after_universe_discovery() {
        let mut form = LoopForm::default();
        form.set("--train", "on".into());
        form.load_snapshots(&json!({"snapshots": [{
            "path": "a",
            "earliest_training_start": "2017-01-01",
            "buckets": {"all": 10}
        }]}));
        form.cursor = form
            .visible()
            .iter()
            .position(|i| *i == index("snapshot"))
            .unwrap();
        form.edit(KeyCode::Right);
        assert_eq!(form.value("train-start"), "2017-01-01");
    }

    #[test]
    fn snapshot_selection_resets_bucket_and_edits_invalidate_preview() {
        let mut form = LoopForm::default();
        form.load_snapshots(&json!({"snapshots": [
            {"path": "a", "buckets": {"all": 10, "bucket-001": 4}},
            {"path": "b", "buckets": {"all": 20}}
        ]}));
        form.set("snapshot", "a".into());
        form.load_buckets();
        form.set("bucket", "bucket-001".into());
        form.preview = Some(json!({}));
        form.cursor = form
            .visible()
            .iter()
            .position(|i| *i == index("snapshot"))
            .unwrap();
        form.edit(KeyCode::Right);
        assert_eq!(form.value("snapshot"), "b");
        assert_eq!(form.value("bucket"), "");
        assert_eq!(form.options[index("bucket")], vec!["all"]);
        assert!(form.preview.is_none());
        form.load_snapshots(&json!({"snapshots": []}));
        assert_eq!(form.value("snapshot"), "");
        assert!(form.options[index("bucket")].is_empty());
    }
}
