//! The resolved config as a browsable tree, plus the value formatting and
//! leaf diffing shared by the config and promotion screens.
//!
//! Objects are branches; scalars **and lists** are leaves — a list is edited
//! and promoted as one value, exactly like the layer system replaces lists
//! wholesale.

use std::collections::{BTreeMap, HashSet};

use serde_json::Value;

/// One visible line of the tree.
pub struct Row {
    /// dot-path into the config (`risk.max_open_positions`)
    pub path: String,
    /// the last path segment
    pub label: String,
    pub depth: usize,
    /// `Some(expanded)` for objects, `None` for leaves
    pub expanded: Option<bool>,
    /// rendered leaf value (empty for branches)
    pub value: String,
}

impl Row {
    pub fn is_leaf(&self) -> bool {
        self.expanded.is_none()
    }
}

/// A config tree with its expansion state and cursor.
pub struct Tree {
    pub config: Value,
    pub profile: String,
    pub hash: String,
    expanded: HashSet<String>,
    pub rows: Vec<Row>,
    pub cursor: usize,
}

impl Tree {
    pub fn new(profile: String, config: Value, hash: String) -> Self {
        let mut tree = Tree {
            config,
            profile,
            hash,
            expanded: HashSet::new(),
            rows: Vec::new(),
            cursor: 0,
        };
        tree.rebuild();
        tree
    }

    /// Replace the config (after an override or a promotion) while keeping
    /// the expansion state and, as far as possible, the cursor.
    pub fn update(&mut self, config: Value, hash: String) {
        self.config = config;
        self.hash = hash;
        self.rebuild();
    }

    fn rebuild(&mut self) {
        let mut rows = Vec::new();
        build_rows(&self.config, "", 0, &self.expanded, &mut rows);
        self.rows = rows;
        if self.cursor >= self.rows.len() {
            self.cursor = self.rows.len().saturating_sub(1);
        }
    }

    pub fn selected(&self) -> Option<&Row> {
        self.rows.get(self.cursor)
    }

    pub fn move_cursor(&mut self, delta: isize) {
        if self.rows.is_empty() {
            return;
        }
        let last = (self.rows.len() - 1) as isize;
        self.cursor = (self.cursor as isize + delta).clamp(0, last) as usize;
    }

    /// Expand or collapse the selected branch.
    pub fn toggle(&mut self) {
        let Some(row) = self.rows.get(self.cursor) else {
            return;
        };
        if row.is_leaf() {
            return;
        }
        let path = row.path.clone();
        if !self.expanded.remove(&path) {
            self.expanded.insert(path);
        }
        self.rebuild();
    }

    /// Collapse everything (`z`) — the fastest way back to an overview.
    pub fn collapse_all(&mut self) {
        self.expanded.clear();
        self.cursor = 0;
        self.rebuild();
    }
}

fn build_rows(
    node: &Value,
    prefix: &str,
    depth: usize,
    expanded: &HashSet<String>,
    rows: &mut Vec<Row>,
) {
    let Some(map) = node.as_object() else { return };
    for (key, child) in map {
        let path = if prefix.is_empty() {
            key.clone()
        } else {
            format!("{prefix}.{key}")
        };
        let branch = child.as_object().is_some_and(|inner| !inner.is_empty());
        let is_expanded = branch && expanded.contains(&path);
        rows.push(Row {
            label: key.clone(),
            depth,
            expanded: branch.then_some(is_expanded),
            value: if branch { String::new() } else { render(child) },
            path: path.clone(),
        });
        if is_expanded {
            build_rows(child, &path, depth + 1, expanded, rows);
        }
    }
}

/// A leaf value as the CLI would accept it in `--set path=value`.
///
/// Strings stay unquoted, lists and empty objects use JSON flow style
/// (which is valid YAML), so the text round-trips through `yaml.safe_load`.
pub fn render(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "null".to_string(),
        Value::Bool(flag) => flag.to_string(),
        Value::Number(number) => number.to_string(),
        other => serde_json::to_string(other).unwrap_or_default(),
    }
}

/// Parse user input the same way the CLI does (`yaml.safe_load`), so what
/// the TUI shows and what Python stores agree.
pub fn parse(text: &str) -> Result<Value, String> {
    serde_yaml::from_str::<Value>(text).map_err(|err| format!("not a valid value: {err}"))
}

/// Every leaf of a config as `dot.path -> value` (lists are leaves).
pub fn flatten(config: &Value) -> BTreeMap<String, Value> {
    let mut flat = BTreeMap::new();
    walk(config, "", &mut flat);
    flat
}

fn walk(node: &Value, prefix: &str, flat: &mut BTreeMap<String, Value>) {
    match node.as_object() {
        Some(map) if !map.is_empty() => {
            for (key, child) in map {
                let path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{prefix}.{key}")
                };
                walk(child, &path, flat);
            }
        }
        _ => {
            if !prefix.is_empty() {
                flat.insert(prefix.to_string(), node.clone());
            }
        }
    }
}

/// One differing leaf between two configs.
pub struct Change {
    pub path: String,
    /// value in the target (`None` when the target has no such leaf)
    pub from: Option<Value>,
    /// value in the source
    pub to: Value,
}

impl Change {
    pub fn old_text(&self) -> String {
        self.from
            .as_ref()
            .map(render)
            .unwrap_or_else(|| "—".to_string())
    }

    pub fn new_text(&self) -> String {
        render(&self.to)
    }
}

/// Leaves of `source` that `target` does not already have with that value.
pub fn changes(source: &Value, target: &Value) -> Vec<Change> {
    let (source, target) = (flatten(source), flatten(target));
    source
        .into_iter()
        .filter(|(path, value)| target.get(path) != Some(value))
        .map(|(path, value)| Change {
            from: target.get(&path).cloned(),
            path,
            to: value,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn config() -> Value {
        json!({
            "symbols": ["AAPL", "MSFT"],
            "risk": {"max_open_positions": 8, "buying_power_buffer": 0.95},
            "regime": {"active": true, "regimes": {"range_bound": {"strategy": "mean_reversion"}}},
            "backtest": {"permutation": null},
            "optimization": {"search_space": {}}
        })
    }

    #[test]
    fn top_level_starts_collapsed_with_leaves_visible() {
        let tree = Tree::new("backtest".into(), config(), "h".into());
        let paths: Vec<&str> = tree.rows.iter().map(|row| row.path.as_str()).collect();
        assert_eq!(
            paths,
            vec!["backtest", "optimization", "regime", "risk", "symbols"]
        );
        let symbols = tree.rows.iter().find(|row| row.path == "symbols").unwrap();
        assert!(symbols.is_leaf(), "a list is one editable value");
        assert_eq!(symbols.value, r#"["AAPL","MSFT"]"#);
    }

    #[test]
    fn toggling_expands_and_collapses_nested_objects() {
        let mut tree = Tree::new("backtest".into(), config(), "h".into());
        tree.cursor = 3; // risk
        tree.toggle();
        let paths: Vec<&str> = tree.rows.iter().map(|row| row.path.as_str()).collect();
        assert!(paths.contains(&"risk.max_open_positions"));
        tree.toggle();
        assert!(!tree
            .rows
            .iter()
            .any(|row| row.path == "risk.max_open_positions"));
    }

    #[test]
    fn empty_objects_are_leaves_not_branches() {
        let tree = Tree::new("backtest".into(), config(), "h".into());
        let mut tree = tree;
        tree.cursor = 1; // optimization
        tree.toggle();
        let row = tree
            .rows
            .iter()
            .find(|row| row.path == "optimization.search_space")
            .unwrap();
        assert!(row.is_leaf());
        assert_eq!(row.value, "{}");
    }

    #[test]
    fn collapse_all_returns_to_the_overview() {
        let mut tree = Tree::new("backtest".into(), config(), "h".into());
        tree.cursor = 3;
        tree.toggle();
        tree.collapse_all();
        assert_eq!(tree.rows.len(), 5);
        assert_eq!(tree.cursor, 0);
    }

    #[test]
    fn cursor_stays_inside_the_rows() {
        let mut tree = Tree::new("backtest".into(), config(), "h".into());
        tree.move_cursor(-5);
        assert_eq!(tree.cursor, 0);
        tree.move_cursor(500);
        assert_eq!(tree.cursor, tree.rows.len() - 1);
    }

    #[test]
    fn values_render_as_the_cli_accepts_them() {
        assert_eq!(render(&json!("mean_reversion")), "mean_reversion");
        assert_eq!(render(&json!(-1.1)), "-1.1");
        assert_eq!(render(&json!(true)), "true");
        assert_eq!(render(&json!(null)), "null");
        assert_eq!(render(&json!(["A", "B"])), r#"["A","B"]"#);
    }

    #[test]
    fn parsing_follows_yaml_scalar_rules() {
        assert_eq!(parse("12").unwrap(), json!(12));
        assert_eq!(parse("-1.5").unwrap(), json!(-1.5));
        assert_eq!(parse("true").unwrap(), json!(true));
        assert_eq!(parse("null").unwrap(), json!(null));
        assert_eq!(parse("[AAPL, MSFT]").unwrap(), json!(["AAPL", "MSFT"]));
        assert_eq!(parse("mean_reversion").unwrap(), json!("mean_reversion"));
        assert!(parse("[unclosed").is_err());
    }

    #[test]
    fn flatten_treats_lists_and_empty_maps_as_leaves() {
        let flat = flatten(&config());
        assert_eq!(flat["symbols"], json!(["AAPL", "MSFT"]));
        assert_eq!(
            flat["regime.regimes.range_bound.strategy"],
            json!("mean_reversion")
        );
        assert_eq!(flat["backtest.permutation"], json!(null));
        assert_eq!(flat["optimization.search_space"], json!({}));
    }

    #[test]
    fn changes_report_only_differing_leaves() {
        let target = json!({
            "symbols": ["AAPL", "MSFT"],
            "risk": {"max_open_positions": 5}
        });
        let source = json!({
            "symbols": ["SPY"],
            "risk": {"max_open_positions": 5, "max_daily_loss_pct": 0.03}
        });
        let changes = changes(&source, &target);
        let paths: Vec<&str> = changes.iter().map(|c| c.path.as_str()).collect();
        assert_eq!(paths, vec!["risk.max_daily_loss_pct", "symbols"]);
        assert_eq!(changes[0].old_text(), "—"); // absent in the target
        assert_eq!(changes[1].old_text(), r#"["AAPL","MSFT"]"#);
        assert_eq!(changes[1].new_text(), r#"["SPY"]"#);
    }
}
