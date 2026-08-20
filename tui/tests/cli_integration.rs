//! Integration tests against the real Python CLI.
//!
//! These spawn `uv run carcharoth ...` and therefore need the project's
//! virtualenv; they are `#[ignore]`d so `cargo test` stays hermetic. Run
//! them from the repository root with:
//!
//! ```text
//! cargo test --workspace -- --ignored
//! ```

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::OnceLock;
use std::thread::sleep;
use std::time::{Duration, Instant};

use carcharoth_tui::runner::{Runner, Status};
use carcharoth_tui::{catalog, cli, config_tree, promote};

/// The CLI resolves `config/` and `.env` relative to the working directory,
/// and cargo runs integration tests from the crate directory — step up once,
/// exactly like the binary insists on being launched from the repo root.
fn repo_root() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        if !Path::new("config/base.yaml").is_file() {
            std::env::set_current_dir("..").expect("repository root above the crate");
        }
        assert!(
            Path::new("config/base.yaml").is_file(),
            "run from the repository"
        );
    });
}

/// Wait for a started command to finish (the event loop's poll, sped up).
fn wait(runner: &mut Runner) {
    let deadline = Instant::now() + Duration::from_secs(120);
    while runner.is_running() && Instant::now() < deadline {
        runner.poll();
        sleep(Duration::from_millis(20));
    }
    runner.poll();
    assert!(!runner.is_running(), "command did not finish in time");
}

#[test]
#[ignore = "spawns the Python CLI"]
fn resolving_a_profile_yields_a_usable_tree() {
    repo_root();
    let resolved = cli::resolve("backtest", &[]).expect("backtest resolves");
    assert!(!resolved.hash.is_empty());
    let flat = config_tree::flatten(&resolved.config);
    assert!(flat.contains_key("data.start"));
    assert!(flat.contains_key("risk.max_open_positions"));
    assert!(flat["symbols"].is_array(), "lists stay one leaf");
}

#[test]
#[ignore = "spawns the Python CLI"]
fn overrides_change_the_resolved_config_and_its_hash() {
    repo_root();
    let plain = cli::resolve("backtest", &[]).unwrap();
    let overrides = vec![("risk.max_open_positions".to_string(), "12".to_string())];
    let overridden = cli::resolve("backtest", &overrides).unwrap();
    assert_ne!(plain.hash, overridden.hash);
    assert_eq!(
        config_tree::flatten(&overridden.config)["risk.max_open_positions"],
        serde_json::json!(12)
    );
}

#[test]
#[ignore = "spawns the Python CLI"]
fn validation_accepts_good_values_and_explains_bad_ones() {
    repo_root();
    let good = vec![("risk.max_open_positions".to_string(), "12".to_string())];
    assert!(matches!(
        cli::validate("backtest", &good).unwrap(),
        cli::Validation::Valid { .. }
    ));

    let bad = vec![(
        "risk.max_position_pct_equity".to_string(),
        "1.5".to_string(),
    )];
    match cli::validate("backtest", &bad).unwrap() {
        cli::Validation::Invalid { errors } => {
            assert!(errors
                .iter()
                .any(|(path, _)| path == "risk.max_position_pct_equity"));
        }
        cli::Validation::Valid { .. } => panic!("1.5 is out of range"),
    }

    let typo = vec![("risk.max_open_positionz".to_string(), "12".to_string())];
    assert!(matches!(
        cli::validate("backtest", &typo).unwrap(),
        cli::Validation::Invalid { .. }
    ));
}

#[test]
#[ignore = "spawns the Python CLI"]
fn the_promotion_target_resolves_through_the_generated_layer() {
    repo_root();
    let paper = cli::resolve(promote::PAPER_PROFILE, &[]).expect("paper resolves");
    assert!(!paper.hash.is_empty());
    // The committed layer exists and parses (empty until something is promoted).
    let promoted = promote::load(&promote::promoted_layer_path()).expect("layer parses");
    let overrides = promote::candidate_overrides(&promoted);
    assert!(matches!(
        cli::validate(promote::PAPER_PROFILE, &overrides).unwrap(),
        cli::Validation::Valid { .. }
    ));
}

#[test]
#[ignore = "spawns the Python CLI"]
fn promotion_writes_only_when_the_candidate_validates() {
    repo_root();
    let path = std::env::temp_dir().join(format!("carcharoth-promote-{}.yaml", std::process::id()));
    let _ = std::fs::remove_file(&path);

    let mut invalid = BTreeMap::new();
    invalid.insert(
        "risk.max_position_pct_equity".to_string(),
        serde_json::json!(1.5),
    );
    assert!(promote::apply(&path, &invalid).is_err());
    assert!(!path.exists(), "nothing is written when invalid");

    let mut valid = BTreeMap::new();
    valid.insert("risk.max_open_positions".to_string(), serde_json::json!(6));
    promote::apply(&path, &valid).expect("valid candidate is written");
    assert_eq!(promote::load(&path).unwrap(), valid);
    std::fs::remove_file(&path).unwrap();
}

/// The catalog is hand-mirrored from `main.py`; this catches drift by
/// asking argparse itself whether every subcommand and flag still exists.
#[test]
#[ignore = "spawns the Python CLI"]
fn every_catalog_entry_matches_the_real_cli() {
    repo_root();
    for spec in catalog::COMMANDS {
        let mut args: Vec<String> = spec.args.iter().map(|arg| arg.to_string()).collect();
        args.push("--help".to_string());
        let help = cli::capture(&args).expect("the CLI runs");
        assert!(
            help.ok,
            "`{}` is not a CLI command: {}",
            spec.name, help.stderr
        );
        for field in spec.fields {
            assert!(
                help.stdout.contains(field.flag),
                "{} does not accept {} ({})",
                spec.name,
                field.flag,
                field.label
            );
        }
    }
}

#[test]
#[ignore = "spawns the Python CLI"]
fn running_a_command_streams_output_and_reports_success() {
    repo_root();
    let mut runner = Runner::default();
    runner
        .start(&["config".to_string(), "list".to_string()])
        .unwrap();
    assert!(runner.is_running());
    // One command at a time.
    assert!(runner
        .start(&["config".to_string(), "list".to_string()])
        .is_err());
    wait(&mut runner);
    assert_eq!(runner.status, Status::Succeeded);
    assert!(runner.lines.iter().any(|line| line.contains("profiles:")));
    assert!(runner
        .lines
        .first()
        .unwrap()
        .contains("uv run carcharoth config list"));
}

#[test]
#[ignore = "spawns the Python CLI"]
fn a_failing_command_reports_its_exit_code() {
    repo_root();
    let mut runner = Runner::default();
    runner
        .start(&[
            "config".to_string(),
            "validate".to_string(),
            "-p".to_string(),
            "does-not-exist".to_string(),
        ])
        .unwrap();
    wait(&mut runner);
    assert!(matches!(runner.status, Status::Failed(_)));
    assert!(runner.status_text().contains("exit code"));
}

#[test]
#[ignore = "spawns the Python CLI"]
fn cancelling_a_command_leaves_no_child_behind() {
    repo_root();
    let mut runner = Runner::default();
    runner
        .start(&[
            "quicktest".to_string(),
            "-p".to_string(),
            "smoke".to_string(),
        ])
        .unwrap();
    sleep(Duration::from_millis(500));
    runner.cancel().expect("running command is cancellable");
    wait(&mut runner);
    assert_eq!(runner.status, Status::Cancelled);
    assert!(!runner.is_running());
}
