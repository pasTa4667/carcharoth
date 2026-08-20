//! The command catalog: every Carcharoth CLI operation as a short name
//! plus a small form.
//!
//! Mirrors the argparse tree in `src/carcharoth/main.py`. Names are the
//! shortened wording the TUI shows (`run backtest`), while `args` holds the
//! real subcommand path handed to the CLI (`["backtest"]`).

use serde_json::Value;

/// Choice option that emits nothing at all.
pub const OMIT: &str = "off";
/// Choice option that emits its flag without a value (argparse `nargs="?"`).
pub const BARE: &str = "(config default)";

/// What a single form field contributes to the command line.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// `on`/`off`; `on` emits the bare flag.
    Flag,
    /// Free text; emits `flag value`, or nothing when empty.
    Text,
    /// One of a fixed set; [`OMIT`] emits nothing, [`BARE`] emits the flag alone.
    Choice(&'static [&'static str]),
}

/// One form field of a command.
pub struct Field {
    /// stable identifier used for prefilling (e.g. `start`, `n-trials`)
    pub key: &'static str,
    pub label: &'static str,
    /// the CLI flag this field feeds; `None` for positional-free fields
    pub flag: &'static str,
    pub kind: Kind,
    /// submitting with an empty value is refused
    pub required: bool,
    pub help: &'static str,
}

impl Field {
    const fn flag(key: &'static str, label: &'static str, help: &'static str) -> Self {
        Field {
            key,
            label,
            flag: key,
            kind: Kind::Flag,
            required: false,
            help,
        }
    }

    const fn text(
        key: &'static str,
        label: &'static str,
        flag: &'static str,
        help: &'static str,
    ) -> Self {
        Field {
            key,
            label,
            flag,
            kind: Kind::Text,
            required: false,
            help,
        }
    }

    const fn required_text(
        key: &'static str,
        label: &'static str,
        flag: &'static str,
        help: &'static str,
    ) -> Self {
        Field {
            key,
            label,
            flag,
            kind: Kind::Text,
            required: true,
            help,
        }
    }

    const fn choice(
        key: &'static str,
        label: &'static str,
        flag: &'static str,
        options: &'static [&'static str],
        help: &'static str,
    ) -> Self {
        Field {
            key,
            label,
            flag,
            kind: Kind::Choice(options),
            required: false,
            help,
        }
    }

    /// The value a freshly opened form starts with.
    pub fn initial(&self) -> String {
        match self.kind {
            Kind::Flag => "off".to_string(),
            Kind::Text => String::new(),
            Kind::Choice(options) => options[0].to_string(),
        }
    }
}

/// One catalog entry: a runnable command with its form.
pub struct CommandSpec {
    /// short, typo-friendly name shown in the list (`run backtest`)
    pub name: &'static str,
    /// the real CLI subcommand path
    pub args: &'static [&'static str],
    pub help: &'static str,
    /// command takes `-p/--profile` and `--set`, so it participates in the
    /// config screen's temporary overrides
    pub profiled: bool,
    /// default profile when the user does not override it
    pub default_profile: Option<&'static str>,
    /// commands that touch live trading or delete data need a confirmation
    pub confirm: Option<&'static str>,
    pub fields: &'static [Field],
}

const PERMUTE_METHODS: &[&str] = &[OMIT, BARE, "in_sample_bars", "monte_carlo_trades"];
const FORMATS: &[&str] = &["yaml", "json"];

const PROFILE_FIELD: Field = Field::text(
    "profile",
    "profile",
    "-p",
    "config profile; prefilled with the command's default",
);

const BACKTEST_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::text(
        "start",
        "start date",
        "--start",
        "YYYY-MM-DD; overrides data.start",
    ),
    Field::text(
        "end",
        "end date",
        "--end",
        "YYYY-MM-DD, inclusive; overrides data.end",
    ),
    Field::text(
        "symbols",
        "symbols",
        "--symbols",
        "comma-separated override of the symbol universe",
    ),
    Field::choice(
        "permute",
        "permute",
        "--permute",
        PERMUTE_METHODS,
        "monte carlo the finished backtest's trades (trade-based methods only)",
    ),
    Field::flag("--verbose", "verbose", "INFO console logging"),
    Field::flag(
        "--no-hmm-cache",
        "no HMM cache",
        "disable the persistent HMM fit cache",
    ),
    Field::flag(
        "--verbose-db",
        "verbose DB",
        "also persist decisions, snapshots, regime evaluations",
    ),
];

const QUICKTEST_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::choice(
        "permute",
        "permute",
        "--permute",
        PERMUTE_METHODS,
        "run a permutation test around the quicktest",
    ),
    Field::text(
        "start",
        "start date",
        "--start",
        "YYYY-MM-DD; overrides data.start",
    ),
    Field::text(
        "end",
        "end date",
        "--end",
        "YYYY-MM-DD, inclusive; overrides data.end",
    ),
    Field::text(
        "workers",
        "workers",
        "--workers",
        "permutation worker processes (0 = auto)",
    ),
    Field::flag("--verbose", "verbose", "INFO console logging"),
];

const OPTIMIZE_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::text(
        "start",
        "start date",
        "--start",
        "YYYY-MM-DD; overrides data.start",
    ),
    Field::text(
        "end",
        "end date",
        "--end",
        "YYYY-MM-DD, inclusive; overrides data.end",
    ),
    Field::text(
        "n-trials",
        "trials",
        "--n-trials",
        "overrides optimization.study.n_trials",
    ),
    Field::text(
        "study-name",
        "study name",
        "--study-name",
        "overrides optimization.study.name",
    ),
    Field::text(
        "workers",
        "workers",
        "--workers",
        "overrides optimization.study.workers",
    ),
    Field::flag("--verbose", "verbose", "INFO console logging"),
    Field::flag(
        "--no-hmm-cache",
        "no HMM cache",
        "use when the study searches HMM params",
    ),
];

const RUN_ID_FIELDS: &[Field] = &[Field::required_text(
    "run-id", "run id", "--run-id", "run UUID",
)];

const CACHE_CLEAR_FIELDS: &[Field] = &[
    Field::flag("--bars", "bars only", "clear only the bars cache"),
    Field::flag("--hmm", "HMM only", "clear only the HMM fit cache"),
];

const CONFIG_RESOLVE_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::choice("format", "format", "--format", FORMATS, "output format"),
];

const CONFIG_VALIDATE_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::flag("--json", "json", "machine-readable output"),
];

const CONFIG_DIFF_FIELDS: &[Field] = &[
    PROFILE_FIELD,
    Field::text(
        "against",
        "against",
        "--against",
        "profile or layer to diff from (default: base)",
    ),
];

const PROFILE_ONLY: &[Field] = &[PROFILE_FIELD];
const NO_FIELDS: &[Field] = &[];

/// Every command the TUI can run, in menu order.
pub const COMMANDS: &[CommandSpec] = &[
    CommandSpec {
        name: "run live",
        args: &["run"],
        help: "live paper trading (Alpaca paper account)",
        profiled: true,
        default_profile: Some("trading/paper"),
        confirm: Some("Start live paper trading?"),
        fields: PROFILE_ONLY,
    },
    CommandSpec {
        name: "run backtest",
        args: &["backtest"],
        help: "replay history through the full engine (regime + risk)",
        profiled: true,
        default_profile: Some("backtest"),
        confirm: None,
        fields: BACKTEST_FIELDS,
    },
    CommandSpec {
        name: "run quicktest",
        args: &["quicktest"],
        help: "isolated strategy test — no engine, regime, or risk",
        profiled: true,
        default_profile: Some("quicktest"),
        confirm: None,
        fields: QUICKTEST_FIELDS,
    },
    CommandSpec {
        name: "run optimize",
        args: &["optimize"],
        help: "Optuna parameter search over backtests",
        profiled: true,
        default_profile: Some("optimization"),
        confirm: None,
        fields: OPTIMIZE_FIELDS,
    },
    CommandSpec {
        name: "run analyze",
        args: &["analyze"],
        help: "recompute metrics for a run",
        profiled: false,
        default_profile: None,
        confirm: None,
        fields: RUN_ID_FIELDS,
    },
    CommandSpec {
        name: "delete run",
        args: &["delete-run"],
        help: "delete a run and all of its data",
        profiled: false,
        default_profile: None,
        confirm: Some("Delete this run and all of its data?"),
        fields: RUN_ID_FIELDS,
    },
    CommandSpec {
        name: "delete all backtests",
        args: &["delete-run", "--all-backtests"],
        help: "delete every backtest run and its data",
        profiled: false,
        default_profile: None,
        confirm: Some("Delete ALL backtest runs?"),
        fields: NO_FIELDS,
    },
    CommandSpec {
        name: "delete all quicktests",
        args: &["delete-run", "--all-quicktests"],
        help: "delete every quicktest run and its data",
        profiled: false,
        default_profile: None,
        confirm: Some("Delete ALL quicktest runs?"),
        fields: NO_FIELDS,
    },
    CommandSpec {
        name: "cache stats",
        args: &["cache", "stats"],
        help: "entry counts per cache + redis memory usage",
        profiled: false,
        default_profile: None,
        confirm: None,
        fields: NO_FIELDS,
    },
    CommandSpec {
        name: "cache clear",
        args: &["cache", "clear"],
        help: "delete cached entries (both caches by default)",
        profiled: false,
        default_profile: None,
        confirm: Some("Clear the Redis cache?"),
        fields: CACHE_CLEAR_FIELDS,
    },
    CommandSpec {
        name: "config list",
        args: &["config", "list"],
        help: "available profiles, symbol sets, presets, search spaces",
        profiled: false,
        default_profile: None,
        confirm: None,
        fields: NO_FIELDS,
    },
    CommandSpec {
        name: "config resolve",
        args: &["config", "resolve"],
        help: "print the fully merged, validated config",
        profiled: true,
        default_profile: Some("backtest"),
        confirm: None,
        fields: CONFIG_RESOLVE_FIELDS,
    },
    CommandSpec {
        name: "config validate",
        args: &["config", "validate"],
        help: "validate a profile (exit 1 when invalid)",
        profiled: true,
        default_profile: Some("backtest"),
        confirm: None,
        fields: CONFIG_VALIDATE_FIELDS,
    },
    CommandSpec {
        name: "config diff",
        args: &["config", "diff"],
        help: "leaf-level diff between two merged configs",
        profiled: true,
        default_profile: Some("backtest"),
        confirm: None,
        fields: CONFIG_DIFF_FIELDS,
    },
    CommandSpec {
        name: "config hash",
        args: &["config", "hash"],
        help: "content hash of the resolved config",
        profiled: true,
        default_profile: Some("backtest"),
        confirm: None,
        fields: PROFILE_ONLY,
    },
    CommandSpec {
        name: "config schema",
        args: &["config", "schema"],
        help: "config JSON Schema (for tooling)",
        profiled: false,
        default_profile: None,
        confirm: None,
        fields: NO_FIELDS,
    },
];

/// A command's form: the current value of every field.
pub struct Form {
    pub spec: usize,
    pub values: Vec<String>,
    pub cursor: usize,
}

impl Form {
    /// A fresh form for a catalog entry, with defaults filled in.
    pub fn new(spec_index: usize) -> Self {
        let spec = &COMMANDS[spec_index];
        let mut values: Vec<String> = spec.fields.iter().map(Field::initial).collect();
        if let (Some(index), Some(profile)) = (index_of(spec, "profile"), spec.default_profile) {
            values[index] = profile.to_string();
        }
        Form {
            spec: spec_index,
            values,
            cursor: 0,
        }
    }

    pub fn spec(&self) -> &'static CommandSpec {
        &COMMANDS[self.spec]
    }

    pub fn field(&self, key: &str) -> Option<&str> {
        index_of(self.spec(), key).map(|index| self.values[index].as_str())
    }

    fn set(&mut self, key: &str, value: String) {
        if let Some(index) = index_of(self.spec(), key) {
            self.values[index] = value;
        }
    }

    /// The profile this form runs against (empty when the command has none).
    pub fn profile(&self) -> String {
        self.field("profile").unwrap_or_default().to_string()
    }

    /// Prefill dates and study knobs from a resolved config, leaving
    /// anything the user already typed untouched.
    pub fn prefill_from(&mut self, config: &Value) {
        for (key, path) in [
            ("start", &["data", "start"][..]),
            ("end", &["data", "end"][..]),
            ("n-trials", &["optimization", "study", "n_trials"][..]),
            ("workers", &["optimization", "study", "workers"][..]),
            ("study-name", &["optimization", "study", "name"][..]),
        ] {
            if !self.field(key).unwrap_or("").is_empty() {
                continue;
            }
            if let Some(text) = scalar_at(config, path) {
                self.set(key, text);
            }
        }
    }

    /// Fields whose value is required but still empty.
    pub fn missing_required(&self) -> Vec<&'static str> {
        self.spec()
            .fields
            .iter()
            .zip(&self.values)
            .filter(|(field, value)| field.required && value.trim().is_empty())
            .map(|(field, _)| field.label)
            .collect()
    }

    /// Move the choice/flag under the cursor to its next (or previous) option.
    pub fn cycle(&mut self, forward: bool) {
        let field = &self.spec().fields[self.cursor];
        let options: &[&str] = match field.kind {
            Kind::Flag => &["off", "on"],
            Kind::Choice(options) => options,
            Kind::Text => return,
        };
        let current = options
            .iter()
            .position(|option| *option == self.values[self.cursor])
            .unwrap_or(0);
        let next = if forward {
            (current + 1) % options.len()
        } else {
            (current + options.len() - 1) % options.len()
        };
        self.values[self.cursor] = options[next].to_string();
    }

    /// The full CLI argument vector: subcommand path, form fields, and the
    /// config screen's temporary overrides (profiled commands only).
    pub fn args(&self, overrides: &[(String, String)]) -> Vec<String> {
        let spec = self.spec();
        let mut args: Vec<String> = spec.args.iter().map(|arg| arg.to_string()).collect();
        for (field, value) in spec.fields.iter().zip(&self.values) {
            let value = value.trim();
            match field.kind {
                Kind::Flag => {
                    if value == "on" {
                        args.push(field.flag.to_string());
                    }
                }
                Kind::Text => {
                    if !value.is_empty() {
                        args.push(field.flag.to_string());
                        args.push(value.to_string());
                    }
                }
                Kind::Choice(_) => match value {
                    OMIT => {}
                    BARE => args.push(field.flag.to_string()),
                    other => {
                        args.push(field.flag.to_string());
                        args.push(other.to_string());
                    }
                },
            }
        }
        if spec.profiled {
            args.extend(crate::cli::set_args(overrides));
        }
        args
    }
}

fn index_of(spec: &CommandSpec, key: &str) -> Option<usize> {
    spec.fields.iter().position(|field| field.key == key)
}

/// A scalar at a JSON path, rendered as the CLI would accept it.
fn scalar_at(config: &Value, path: &[&str]) -> Option<String> {
    let mut node = config;
    for segment in path {
        node = node.get(segment)?;
    }
    match node {
        Value::String(text) => Some(text.clone()),
        Value::Number(number) => Some(number.to_string()),
        Value::Bool(flag) => Some(flag.to_string()),
        _ => None,
    }
}

/// Index of a catalog entry by short name.
#[cfg(test)]
pub fn index_by_name(name: &str) -> Option<usize> {
    COMMANDS.iter().position(|spec| spec.name == name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn form(name: &str) -> Form {
        Form::new(index_by_name(name).expect("known command"))
    }

    #[test]
    fn every_command_has_a_unique_name_and_subcommand_path() {
        let mut names: Vec<&str> = COMMANDS.iter().map(|spec| spec.name).collect();
        names.sort_unstable();
        let count = names.len();
        names.dedup();
        assert_eq!(names.len(), count);
        assert!(COMMANDS.iter().all(|spec| !spec.args.is_empty()));
    }

    #[test]
    fn profiled_commands_expose_a_profile_field_and_default() {
        for spec in COMMANDS.iter().filter(|spec| spec.profiled) {
            assert!(
                spec.default_profile.is_some(),
                "{} lacks a default",
                spec.name
            );
            assert!(
                index_of(spec, "profile").is_some(),
                "{} lacks a field",
                spec.name
            );
        }
    }

    #[test]
    fn defaults_produce_the_bare_subcommand() {
        assert_eq!(
            form("run backtest").args(&[]),
            vec!["backtest", "-p", "backtest"]
        );
        assert_eq!(form("cache stats").args(&[]), vec!["cache", "stats"]);
    }

    #[test]
    fn text_flags_and_choices_are_emitted_in_field_order() {
        let mut backtest = form("run backtest");
        backtest.values = vec![
            "smoke".to_string(),
            "2025-01-01".to_string(),
            "2025-06-30".to_string(),
            "AAPL,MSFT".to_string(),
            "monte_carlo_trades".to_string(),
            "on".to_string(),
            "off".to_string(),
            "off".to_string(),
        ];
        assert_eq!(
            backtest.args(&[]),
            vec![
                "backtest",
                "-p",
                "smoke",
                "--start",
                "2025-01-01",
                "--end",
                "2025-06-30",
                "--symbols",
                "AAPL,MSFT",
                "--permute",
                "monte_carlo_trades",
                "--verbose",
            ]
        );
    }

    #[test]
    fn bare_choice_emits_the_flag_alone() {
        let mut quicktest = form("run quicktest");
        quicktest.values[1] = BARE.to_string();
        assert_eq!(
            quicktest.args(&[]),
            vec!["quicktest", "-p", "quicktest", "--permute"]
        );
    }

    #[test]
    fn overrides_are_appended_only_to_profiled_commands() {
        let overrides = vec![("risk.max_open_positions".to_string(), "12".to_string())];
        assert_eq!(
            form("run backtest").args(&overrides),
            vec![
                "backtest",
                "-p",
                "backtest",
                "--set",
                "risk.max_open_positions=12"
            ]
        );
        assert_eq!(form("cache stats").args(&overrides), vec!["cache", "stats"]);
    }

    #[test]
    fn required_fields_are_reported_until_filled() {
        let mut analyze = form("run analyze");
        assert_eq!(analyze.missing_required(), vec!["run id"]);
        analyze.values[0] = "b0a1".to_string();
        assert!(analyze.missing_required().is_empty());
        assert_eq!(analyze.args(&[]), vec!["analyze", "--run-id", "b0a1"]);
    }

    #[test]
    fn prefill_fills_empty_fields_only() {
        let config = json!({
            "data": {"start": "2025-02-01", "end": "2025-03-01"},
            "optimization": {"study": {"n_trials": 20, "workers": 2, "name": "default-study"}}
        });
        let mut backtest = form("run backtest");
        backtest.values[2] = "2025-05-05".to_string();
        backtest.prefill_from(&config);
        assert_eq!(backtest.field("start"), Some("2025-02-01"));
        assert_eq!(backtest.field("end"), Some("2025-05-05"));

        let mut optimize = form("run optimize");
        optimize.prefill_from(&config);
        assert_eq!(optimize.field("n-trials"), Some("20"));
        assert_eq!(optimize.field("workers"), Some("2"));
        assert_eq!(optimize.field("study-name"), Some("default-study"));
    }

    #[test]
    fn cycling_wraps_in_both_directions() {
        let mut quicktest = form("run quicktest");
        quicktest.cursor = 1; // permute
        quicktest.cycle(true);
        assert_eq!(quicktest.field("permute"), Some(BARE));
        quicktest.cycle(false);
        assert_eq!(quicktest.field("permute"), Some(OMIT));
        quicktest.cycle(false);
        assert_eq!(quicktest.field("permute"), Some("monte_carlo_trades"));
    }

    #[test]
    fn destructive_commands_require_confirmation() {
        for name in [
            "run live",
            "delete run",
            "delete all backtests",
            "delete all quicktests",
            "cache clear",
        ] {
            assert!(
                COMMANDS[index_by_name(name).unwrap()].confirm.is_some(),
                "{name}"
            );
        }
    }
}
