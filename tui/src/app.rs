//! Application state and key handling.
//!
//! Four screens (commands, config, promote, output), one modal layer, and a
//! single status line. Everything that touches the trading system goes
//! through the Python CLI in [`crate::cli`] / [`crate::runner`].

use std::collections::BTreeMap;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::catalog::{Form, Kind, COMMANDS};
use crate::cli::{self, Validation};
use crate::config_tree::{self, Change, Tree};
use crate::promote;
use crate::runner::Runner;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    Commands,
    Config,
    Promote,
    Output,
}

impl Screen {
    pub const ALL: [Screen; 4] = [
        Screen::Commands,
        Screen::Config,
        Screen::Promote,
        Screen::Output,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Screen::Commands => "Commands",
            Screen::Config => "Config",
            Screen::Promote => "Promote",
            Screen::Output => "Output",
        }
    }

    fn next(self, forward: bool) -> Screen {
        let index = Screen::ALL.iter().position(|s| *s == self).unwrap_or(0);
        let count = Screen::ALL.len();
        let next = if forward {
            (index + 1) % count
        } else {
            (index + count - 1) % count
        };
        Screen::ALL[next]
    }
}

/// Which pane of the commands screen takes keys.
#[derive(PartialEq, Eq)]
pub enum Focus {
    List,
    Form,
}

/// What a confirmation, once accepted, will do.
pub enum Action {
    Run(Vec<String>),
    Promote,
}

/// What a text prompt, once accepted, will do.
pub enum Prompt {
    /// edit the selected config leaf (as a temporary override)
    EditLeaf { path: String },
    /// switch the config screen to another profile
    ConfigProfile,
    /// switch the promotion source profile
    PromoteSource,
}

pub enum Modal {
    None,
    Help,
    Confirm {
        message: String,
        action: Action,
    },
    Input {
        prompt: Prompt,
        label: String,
        buffer: String,
        error: Option<String>,
    },
}

/// Commands screen state.
pub struct Commands {
    pub cursor: usize,
    pub filter: String,
    pub filtering: bool,
    pub focus: Focus,
    pub form: Form,
}

impl Default for Commands {
    fn default() -> Self {
        Commands {
            cursor: 0,
            filter: String::new(),
            filtering: false,
            focus: Focus::List,
            form: Form::new(0),
        }
    }
}

/// Promotion screen state.
#[derive(Default)]
pub struct Promote {
    pub source: String,
    pub changes: Vec<Change>,
    pub selected: Vec<bool>,
    pub cursor: usize,
    pub promoted: BTreeMap<String, Value>,
    pub loaded: bool,
}

pub struct App {
    pub screen: Screen,
    pub commands: Commands,
    pub config: Option<Tree>,
    pub config_profile: String,
    /// temporary `--set` overrides shared by every profiled command
    pub overrides: Vec<(String, String)>,
    pub promote: Promote,
    pub runner: Runner,
    pub modal: Modal,
    pub status: String,
    /// output pane height, refreshed while rendering so scrolling matches
    pub output_viewport: usize,
    pub should_quit: bool,
}

impl Default for App {
    fn default() -> Self {
        App {
            screen: Screen::Commands,
            commands: Commands::default(),
            config: None,
            config_profile: "backtest".to_string(),
            overrides: Vec::new(),
            promote: Promote {
                source: "backtest".to_string(),
                ..Promote::default()
            },
            runner: Runner::default(),
            modal: Modal::None,
            status: "F1 help · Tab switches screens · q quits".to_string(),
            output_viewport: 20,
            should_quit: false,
        }
    }
}

impl App {
    /// Command indices matching the current filter, in catalog order.
    pub fn filtered(&self) -> Vec<usize> {
        let needle = self.commands.filter.trim().to_lowercase();
        COMMANDS
            .iter()
            .enumerate()
            .filter(|(_, spec)| {
                needle.is_empty()
                    || spec.name.to_lowercase().contains(&needle)
                    || spec.help.to_lowercase().contains(&needle)
            })
            .map(|(index, _)| index)
            .collect()
    }

    /// The catalog entry under the list cursor.
    pub fn selected_command(&self) -> Option<usize> {
        self.filtered().get(self.commands.cursor).copied()
    }

    /// The argument vector the form would run right now.
    pub fn form_args(&self) -> Vec<String> {
        self.commands.form.args(&self.overrides)
    }

    pub fn handle_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.should_quit = true;
            return;
        }
        if !matches!(self.modal, Modal::None) {
            self.modal_key(key);
            return;
        }
        if key.code == KeyCode::F(1) {
            self.modal = Modal::Help;
            return;
        }
        match key.code {
            KeyCode::Tab => self.screen = self.screen.next(true),
            KeyCode::BackTab => self.screen = self.screen.next(false),
            _ => match self.screen {
                Screen::Commands => self.commands_key(key),
                Screen::Config => self.config_key(key),
                Screen::Promote => self.promote_key(key),
                Screen::Output => self.output_key(key),
            },
        }
    }

    // ----------------------------------------------------------------- modal

    fn modal_key(&mut self, key: KeyEvent) {
        match &mut self.modal {
            Modal::Help => {
                self.modal = Modal::None;
            }
            Modal::Confirm { .. } => match key.code {
                KeyCode::Enter | KeyCode::Char('y') => {
                    if let Modal::Confirm { action, .. } =
                        std::mem::replace(&mut self.modal, Modal::None)
                    {
                        self.perform(action);
                    }
                }
                KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('q') => {
                    self.modal = Modal::None;
                    self.status = "cancelled".to_string();
                }
                _ => {}
            },
            Modal::Input { buffer, .. } => match key.code {
                KeyCode::Char(c) => buffer.push(c),
                KeyCode::Backspace => {
                    buffer.pop();
                }
                KeyCode::Esc => self.modal = Modal::None,
                KeyCode::Enter => self.submit_input(),
                _ => {}
            },
            Modal::None => {}
        }
    }

    fn perform(&mut self, action: Action) {
        match action {
            Action::Run(args) => self.start(args),
            Action::Promote => self.apply_promotion(),
        }
    }

    fn submit_input(&mut self) {
        let Modal::Input { prompt, buffer, .. } = std::mem::replace(&mut self.modal, Modal::None)
        else {
            return;
        };
        let text = buffer.trim().to_string();
        match prompt {
            Prompt::EditLeaf { path } => self.apply_override(path, buffer, text),
            Prompt::ConfigProfile => {
                if !text.is_empty() {
                    self.config_profile = text;
                    self.config = None;
                    self.load_config();
                }
            }
            Prompt::PromoteSource => {
                if !text.is_empty() {
                    self.promote.source = text;
                    self.reload_promotion();
                }
            }
        }
    }

    // -------------------------------------------------------------- commands

    fn commands_key(&mut self, key: KeyEvent) {
        if self.commands.filtering {
            match key.code {
                KeyCode::Char(c) => {
                    self.commands.filter.push(c);
                    self.commands.cursor = 0;
                    self.sync_form();
                }
                KeyCode::Backspace => {
                    self.commands.filter.pop();
                    self.commands.cursor = 0;
                    self.sync_form();
                }
                KeyCode::Enter | KeyCode::Esc => self.commands.filtering = false,
                _ => {}
            }
            return;
        }
        if self.commands.focus == Focus::Form {
            self.form_key(key);
            return;
        }
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => self.move_command(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_command(1),
            KeyCode::Char('/') => {
                self.commands.filtering = true;
                self.commands.filter.clear();
            }
            KeyCode::Enter | KeyCode::Right | KeyCode::Char('l') => self.enter_form(),
            KeyCode::Char('q') => self.should_quit = true,
            _ => {}
        }
    }

    fn move_command(&mut self, delta: isize) {
        let count = self.filtered().len();
        if count == 0 {
            return;
        }
        let last = (count - 1) as isize;
        self.commands.cursor = (self.commands.cursor as isize + delta).clamp(0, last) as usize;
        self.sync_form();
    }

    /// Keep the form in step with the highlighted command.
    fn sync_form(&mut self) {
        if let Some(index) = self.selected_command() {
            if self.commands.form.spec != index {
                self.commands.form = Form::new(index);
            }
        }
    }

    /// Enter the form, prefilling suggestions from the resolved profile.
    fn enter_form(&mut self) {
        self.sync_form();
        self.commands.focus = Focus::Form;
        self.commands.form.cursor = 0;
        let spec = self.commands.form.spec();
        if !spec.profiled {
            return;
        }
        let profile = self.commands.form.profile();
        match cli::resolve(&profile, &self.overrides) {
            Ok(resolved) => {
                self.commands.form.prefill_from(&resolved.config);
                self.status = format!("prefilled from profile {profile}");
            }
            Err(err) => self.status = format!("no suggestions: {err}"),
        }
    }

    fn form_key(&mut self, key: KeyEvent) {
        let field_count = self.commands.form.spec().fields.len();
        match key.code {
            KeyCode::Esc => {
                self.commands.focus = Focus::List;
                return;
            }
            KeyCode::Enter => {
                self.submit_form();
                return;
            }
            KeyCode::Up => {
                if field_count > 0 {
                    self.commands.form.cursor = self.commands.form.cursor.saturating_sub(1);
                }
                return;
            }
            KeyCode::Down => {
                if field_count > 0 {
                    self.commands.form.cursor =
                        (self.commands.form.cursor + 1).min(field_count - 1);
                }
                return;
            }
            _ => {}
        }
        if field_count == 0 {
            return;
        }
        let cursor = self.commands.form.cursor;
        let kind = self.commands.form.spec().fields[cursor].kind;
        match (kind, key.code) {
            (Kind::Text, KeyCode::Char(c)) => self.commands.form.values[cursor].push(c),
            (Kind::Text, KeyCode::Backspace) => {
                self.commands.form.values[cursor].pop();
            }
            (_, KeyCode::Right) | (_, KeyCode::Char(' ')) => self.commands.form.cycle(true),
            (_, KeyCode::Left) => self.commands.form.cycle(false),
            _ => {}
        }
    }

    fn submit_form(&mut self) {
        let missing = self.commands.form.missing_required();
        if !missing.is_empty() {
            self.status = format!("missing required: {}", missing.join(", "));
            return;
        }
        let args = self.form_args();
        match self.commands.form.spec().confirm {
            Some(message) => {
                self.modal = Modal::Confirm {
                    message: format!("{message}\n\n{}", cli::display_command(&args)),
                    action: Action::Run(args),
                }
            }
            None => self.start(args),
        }
    }

    fn start(&mut self, args: Vec<String>) {
        match self.runner.start(&args) {
            Ok(()) => {
                self.screen = Screen::Output;
                self.status = format!("running: {}", self.runner.command);
            }
            Err(err) => self.status = err.to_string(),
        }
    }

    // ---------------------------------------------------------------- config

    /// Resolve the config screen's profile with the current overrides.
    pub fn load_config(&mut self) {
        let profile = self.config_profile.clone();
        match cli::resolve(&profile, &self.overrides) {
            Ok(resolved) => {
                match self.config.as_mut() {
                    Some(tree) if tree.profile == profile => {
                        tree.update(resolved.config, resolved.hash)
                    }
                    _ => {
                        self.config =
                            Some(Tree::new(profile.clone(), resolved.config, resolved.hash))
                    }
                }
                self.status = format!("profile {profile} resolved");
            }
            Err(err) => {
                self.config = None;
                self.status = format!("could not resolve {profile}: {err}");
            }
        }
    }

    fn config_key(&mut self, key: KeyEvent) {
        if self.config.is_none() {
            if matches!(key.code, KeyCode::Char('q')) {
                self.should_quit = true;
            } else if matches!(key.code, KeyCode::Char('r') | KeyCode::Enter) {
                self.load_config();
            }
            return;
        }
        match key.code {
            KeyCode::Char('q') => self.should_quit = true,
            KeyCode::Char('r') => self.load_config(),
            KeyCode::Char('p') => {
                self.modal = Modal::Input {
                    prompt: Prompt::ConfigProfile,
                    label: "profile".to_string(),
                    buffer: self.config_profile.clone(),
                    error: None,
                }
            }
            KeyCode::Char('e') => self.edit_selected_leaf(),
            KeyCode::Char('d') => self.drop_selected_override(),
            KeyCode::Char('D') => {
                self.overrides.clear();
                self.load_config();
                self.status = "all temporary overrides dropped".to_string();
            }
            KeyCode::Char('z') => {
                if let Some(tree) = self.config.as_mut() {
                    tree.collapse_all();
                }
            }
            KeyCode::Up | KeyCode::Char('k') => self.config.as_mut().unwrap().move_cursor(-1),
            KeyCode::Down | KeyCode::Char('j') => self.config.as_mut().unwrap().move_cursor(1),
            KeyCode::PageUp => self.config.as_mut().unwrap().move_cursor(-10),
            KeyCode::PageDown => self.config.as_mut().unwrap().move_cursor(10),
            KeyCode::Enter | KeyCode::Char(' ') | KeyCode::Right | KeyCode::Left => {
                let tree = self.config.as_mut().unwrap();
                if tree.selected().is_some_and(|row| row.is_leaf()) {
                    self.edit_selected_leaf();
                } else {
                    tree.toggle();
                }
            }
            _ => {}
        }
    }

    fn edit_selected_leaf(&mut self) {
        let Some(row) = self.config.as_ref().and_then(Tree::selected) else {
            return;
        };
        if !row.is_leaf() {
            self.status = "select a value (leaf) to edit".to_string();
            return;
        }
        self.modal = Modal::Input {
            prompt: Prompt::EditLeaf {
                path: row.path.clone(),
            },
            label: row.path.clone(),
            buffer: row.value.clone(),
            error: None,
        };
    }

    /// Validate an edited leaf against the profile, then keep it as a
    /// temporary `--set` override.
    fn apply_override(&mut self, path: String, raw: String, text: String) {
        if let Err(message) = config_tree::parse(&text) {
            self.reopen_edit(path, raw, message);
            return;
        }
        let mut candidate = self.overrides.clone();
        match candidate.iter_mut().find(|(other, _)| *other == path) {
            Some(entry) => entry.1 = text.clone(),
            None => candidate.push((path.clone(), text.clone())),
        }
        match cli::validate(&self.config_profile, &candidate) {
            Ok(Validation::Valid { .. }) => {
                self.overrides = candidate;
                self.overrides.sort_by(|a, b| a.0.cmp(&b.0));
                self.load_config();
                self.status = format!("{path} = {text} (temporary override)");
            }
            Ok(invalid) => self.reopen_edit(path, raw, invalid.summary()),
            Err(err) => self.reopen_edit(path, raw, err.to_string()),
        }
    }

    /// Show the edit prompt again with the offending value and the reason.
    fn reopen_edit(&mut self, path: String, buffer: String, error: String) {
        self.status = format!("rejected: {error}");
        self.modal = Modal::Input {
            label: path.clone(),
            prompt: Prompt::EditLeaf { path },
            buffer,
            error: Some(error),
        };
    }

    fn drop_selected_override(&mut self) {
        let Some(path) = self
            .config
            .as_ref()
            .and_then(Tree::selected)
            .map(|row| row.path.clone())
        else {
            return;
        };
        let before = self.overrides.len();
        self.overrides.retain(|(other, _)| *other != path);
        if self.overrides.len() == before {
            self.status = format!("{path} has no temporary override");
        } else {
            self.load_config();
            self.status = format!("dropped override {path}");
        }
    }

    // --------------------------------------------------------------- promote

    /// Diff the source profile (with overrides) against paper trading.
    pub fn reload_promotion(&mut self) {
        self.promote.loaded = true;
        let source = self.promote.source.clone();
        let left = match cli::resolve(&source, &self.overrides) {
            Ok(resolved) => resolved.config,
            Err(err) => {
                self.status = format!("could not resolve {source}: {err}");
                self.promote.changes.clear();
                self.promote.selected.clear();
                return;
            }
        };
        let right = match cli::resolve(promote::PAPER_PROFILE, &[]) {
            Ok(resolved) => resolved.config,
            Err(err) => {
                self.status = format!("could not resolve {}: {err}", promote::PAPER_PROFILE);
                self.promote.changes.clear();
                self.promote.selected.clear();
                return;
            }
        };
        self.promote.promoted = promote::load(&promote::promoted_layer_path()).unwrap_or_default();
        self.promote.changes = config_tree::changes(&left, &right);
        self.promote.selected = vec![false; self.promote.changes.len()];
        self.promote.cursor = 0;
        self.status = format!(
            "{} value(s) differ between {source} and {}",
            self.promote.changes.len(),
            promote::PAPER_PROFILE
        );
    }

    fn promote_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Char('q') => self.should_quit = true,
            KeyCode::Char('r') => self.reload_promotion(),
            KeyCode::Char('s') => {
                self.modal = Modal::Input {
                    prompt: Prompt::PromoteSource,
                    label: "source profile".to_string(),
                    buffer: self.promote.source.clone(),
                    error: None,
                }
            }
            KeyCode::Up | KeyCode::Char('k') => self.move_change(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_change(1),
            KeyCode::Char(' ') => {
                if let Some(flag) = self.promote.selected.get_mut(self.promote.cursor) {
                    *flag = !*flag;
                }
            }
            KeyCode::Char('a') => {
                let all = self.promote.selected.iter().all(|flag| *flag);
                self.promote
                    .selected
                    .iter_mut()
                    .for_each(|flag| *flag = !all);
            }
            KeyCode::Enter => self.confirm_promotion(),
            _ => {}
        }
    }

    fn move_change(&mut self, delta: isize) {
        if self.promote.changes.is_empty() {
            return;
        }
        let last = (self.promote.changes.len() - 1) as isize;
        self.promote.cursor = (self.promote.cursor as isize + delta).clamp(0, last) as usize;
    }

    /// The leaves the user ticked, as `path -> value`.
    pub fn promotion_selection(&self) -> Vec<(String, Value)> {
        self.promote
            .changes
            .iter()
            .zip(&self.promote.selected)
            .filter(|(_, selected)| **selected)
            .map(|(change, _)| (change.path.clone(), change.to.clone()))
            .collect()
    }

    fn confirm_promotion(&mut self) {
        let selection = self.promotion_selection();
        if selection.is_empty() {
            self.status = "select values with space first".to_string();
            return;
        }
        let preview = selection
            .iter()
            .take(8)
            .map(|(path, value)| format!("  {path} = {}", config_tree::render(value)))
            .collect::<Vec<_>>()
            .join("\n");
        let more = selection.len().saturating_sub(8);
        self.modal = Modal::Confirm {
            message: format!(
                "Promote {} value(s) to {} ({})?\n{preview}{}",
                selection.len(),
                promote::PAPER_PROFILE,
                promote::PROMOTED_LAYER,
                if more > 0 {
                    format!("\n  … {more} more")
                } else {
                    String::new()
                },
            ),
            action: Action::Promote,
        };
    }

    fn apply_promotion(&mut self) {
        let selection = self.promotion_selection();
        let merged = promote::merge(&self.promote.promoted, selection);
        match promote::apply(&promote::promoted_layer_path(), &merged) {
            Ok(hash) => {
                self.status = format!(
                    "promoted to {} — {} now hashes {hash}",
                    promote::PROMOTED_LAYER,
                    promote::PAPER_PROFILE
                );
                self.reload_promotion();
                if self.config.is_some() {
                    self.load_config();
                }
            }
            Err(err) => self.status = err.to_string(),
        }
    }

    // ---------------------------------------------------------------- output

    fn output_key(&mut self, key: KeyEvent) {
        let viewport = self.output_viewport as isize;
        match key.code {
            KeyCode::Char('q') => self.should_quit = true,
            KeyCode::Char('x') => match self.runner.cancel() {
                Ok(()) => self.status = "cancelling…".to_string(),
                Err(err) => self.status = err.to_string(),
            },
            KeyCode::Up | KeyCode::Char('k') => self.runner.scroll_by(-1, self.output_viewport),
            KeyCode::Down | KeyCode::Char('j') => self.runner.scroll_by(1, self.output_viewport),
            KeyCode::PageUp => self.runner.scroll_by(-viewport, self.output_viewport),
            KeyCode::PageDown => self.runner.scroll_by(viewport, self.output_viewport),
            KeyCode::Home => self.runner.scroll_by(isize::MIN / 2, self.output_viewport),
            KeyCode::End => self.runner.scroll_by(isize::MAX / 2, self.output_viewport),
            _ => {}
        }
    }

    /// Lazy per-screen loading, run once per frame.
    pub fn refresh(&mut self) {
        match self.screen {
            Screen::Config if self.config.is_none() && !self.status.starts_with("could not") => {
                self.load_config()
            }
            Screen::Promote if !self.promote.loaded => self.reload_promotion(),
            _ => {}
        }
    }

    /// Key hints for the current screen.
    pub fn hints(&self) -> &'static str {
        match self.screen {
            Screen::Commands => match (self.commands.filtering, &self.commands.focus) {
                (true, _) => "type to filter · Enter/Esc done",
                (_, Focus::List) => "↑↓ select · Enter open · / filter · Tab screen · q quit",
                (_, Focus::Form) => "↑↓ field · ←→/space toggle · type to edit · Enter run · Esc back",
            },
            Screen::Config => "↑↓ move · Enter expand/edit · e edit · d drop · D drop all · p profile · r reload · z collapse",
            Screen::Promote => "↑↓ move · space select · a all · s source · r reload · Enter promote",
            Screen::Output => "↑↓/PgUp/PgDn scroll · End follow · x cancel · q quit",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::index_by_name as command_index;

    fn command_name(index: usize) -> &'static str {
        COMMANDS[index].name
    }

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    #[test]
    fn tab_cycles_screens_in_both_directions() {
        let mut app = App::default();
        app.handle_key(key(KeyCode::Tab));
        assert!(matches!(app.screen, Screen::Config));
        app.handle_key(key(KeyCode::BackTab));
        assert!(matches!(app.screen, Screen::Commands));
        app.handle_key(key(KeyCode::BackTab));
        assert!(matches!(app.screen, Screen::Output));
    }

    #[test]
    fn filtering_narrows_the_catalog_and_follows_the_cursor() {
        let mut app = App::default();
        app.handle_key(key(KeyCode::Char('/')));
        for c in "cache".chars() {
            app.handle_key(key(KeyCode::Char(c)));
        }
        let names: Vec<&str> = app.filtered().into_iter().map(command_name).collect();
        assert_eq!(names, vec!["cache stats", "cache clear"]);
        app.handle_key(key(KeyCode::Enter)); // leave the filter
        app.handle_key(key(KeyCode::Down));
        assert_eq!(command_name(app.selected_command().unwrap()), "cache clear");
        assert_eq!(app.form_args(), vec!["cache", "clear"]);
    }

    #[test]
    fn form_editing_and_toggling_builds_the_command() {
        let mut app = App::default();
        app.commands.form = Form::new(command_index("run backtest").unwrap());
        app.commands.focus = Focus::Form;
        app.commands.form.cursor = 1; // start date
        for c in "2025-02-01".chars() {
            app.handle_key(key(KeyCode::Char(c)));
        }
        app.handle_key(key(KeyCode::Backspace));
        app.commands.form.cursor = 5; // --verbose
        app.handle_key(key(KeyCode::Char(' ')));
        assert_eq!(
            app.form_args(),
            vec![
                "backtest",
                "-p",
                "backtest",
                "--start",
                "2025-02-0",
                "--verbose"
            ]
        );
    }

    #[test]
    fn required_fields_block_submission_without_touching_the_runner() {
        let mut app = App::default();
        app.commands.form = Form::new(command_index("run analyze").unwrap());
        app.commands.focus = Focus::Form;
        app.handle_key(key(KeyCode::Enter));
        assert!(app.status.contains("missing required"));
        assert!(!app.runner.is_running());
    }

    #[test]
    fn destructive_commands_open_a_confirmation_first() {
        let mut app = App::default();
        app.commands.form = Form::new(command_index("delete all backtests").unwrap());
        app.commands.focus = Focus::Form;
        app.handle_key(key(KeyCode::Enter));
        match &app.modal {
            Modal::Confirm { message, .. } => {
                assert!(message.contains("Delete ALL backtest runs?"));
                assert!(message.contains("uv run carcharoth delete-run --all-backtests"));
            }
            _ => panic!("expected a confirmation"),
        }
        app.handle_key(key(KeyCode::Char('n')));
        assert!(matches!(app.modal, Modal::None));
        assert!(!app.runner.is_running());
    }

    #[test]
    fn ctrl_c_always_quits() {
        let mut app = App {
            modal: Modal::Help,
            ..App::default()
        };
        app.handle_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL));
        assert!(app.should_quit);
    }

    #[test]
    fn help_opens_and_closes() {
        let mut app = App::default();
        app.handle_key(key(KeyCode::F(1)));
        assert!(matches!(app.modal, Modal::Help));
        app.handle_key(key(KeyCode::Esc));
        assert!(matches!(app.modal, Modal::None));
    }

    #[test]
    fn dropping_an_override_reports_when_there_is_none() {
        let mut app = App {
            config: Some(Tree::new(
                "backtest".to_string(),
                serde_json::json!({"risk": {"max_open_positions": 8}}),
                "h".to_string(),
            )),
            ..App::default()
        };
        app.drop_selected_override();
        assert!(app.status.contains("has no temporary override"));
    }

    #[test]
    fn invalid_leaf_values_are_rejected_before_any_cli_call() {
        let mut app = App::default();
        app.apply_override(
            "symbols".to_string(),
            "[SPY".to_string(),
            "[SPY".to_string(),
        );
        assert!(app.overrides.is_empty());
        match &app.modal {
            Modal::Input { error, .. } => {
                assert!(error.as_ref().unwrap().contains("not a valid value"))
            }
            _ => panic!("expected the edit prompt to reopen"),
        }
    }

    #[test]
    fn promotion_needs_a_selection() {
        let mut app = App::default();
        app.confirm_promotion();
        assert!(matches!(app.modal, Modal::None));
        assert!(app.status.contains("select values"));
    }

    #[test]
    fn promotion_selection_follows_the_ticked_rows() {
        let mut app = App::default();
        app.promote.changes = vec![
            Change {
                path: "risk.max_open_positions".to_string(),
                from: Some(serde_json::json!(5)),
                to: serde_json::json!(8),
            },
            Change {
                path: "symbols".to_string(),
                from: None,
                to: serde_json::json!(["SPY"]),
            },
        ];
        app.promote.selected = vec![false, false];
        app.handle_key(key(KeyCode::Tab)); // config
        app.handle_key(key(KeyCode::Tab)); // promote
        app.handle_key(key(KeyCode::Char('a')));
        assert_eq!(app.promotion_selection().len(), 2);
        app.handle_key(key(KeyCode::Char(' ')));
        assert_eq!(
            app.promotion_selection(),
            vec![("symbols".to_string(), serde_json::json!(["SPY"]))]
        );
    }
}
