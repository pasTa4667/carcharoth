//! Rendering. Pure: reads [`App`] (plus the output viewport it records) and
//! draws; all state changes happen in `app.rs`.

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, Paragraph, Tabs, Wrap};
use ratatui::Frame;

use crate::app::{App, Focus, Modal, Screen};
use crate::catalog::{Kind, COMMANDS};
use crate::cli;
use crate::promote;
use crate::runner::Status;

const ACCENT: Color = Color::Cyan;
const DIM: Color = Color::DarkGray;

pub fn draw(frame: &mut Frame, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(3),
        ])
        .split(frame.area());

    draw_tabs(frame, app, chunks[0]);
    match app.screen {
        Screen::Commands => draw_commands(frame, app, chunks[1]),
        Screen::Config => draw_config(frame, app, chunks[1]),
        Screen::Promote => draw_promote(frame, app, chunks[1]),
        Screen::Output => draw_output(frame, app, chunks[1]),
    }
    draw_status(frame, app, chunks[2]);

    match &app.modal {
        Modal::None => {}
        Modal::Help => draw_modal(frame, "Help", &help_text(), None),
        Modal::Confirm { message, .. } => draw_modal(
            frame,
            "Confirm",
            message,
            Some("Enter/y confirm · Esc/n cancel"),
        ),
        Modal::Input {
            label,
            buffer,
            error,
            ..
        } => {
            let body = format!("{label}\n\n> {buffer}");
            let footer = error
                .clone()
                .unwrap_or_else(|| "Enter accept · Esc cancel".to_string());
            draw_modal(frame, "Edit value", &body, Some(&footer));
        }
    }
}

fn draw_tabs(frame: &mut Frame, app: &App, area: Rect) {
    let titles: Vec<Line> = Screen::ALL
        .iter()
        .enumerate()
        .map(|(index, screen)| Line::from(format!(" {}. {} ", index + 1, screen.title())))
        .collect();
    let selected = Screen::ALL
        .iter()
        .position(|s| *s == app.screen)
        .unwrap_or(0);
    let tabs = Tabs::new(titles)
        .select(selected)
        .block(Block::default().borders(Borders::ALL).title(" carcharoth "))
        .highlight_style(Style::default().fg(ACCENT).add_modifier(Modifier::BOLD));
    frame.render_widget(tabs, area);
}

fn draw_status(frame: &mut Frame, app: &App, area: Rect) {
    let running = if app.runner.is_running() {
        Span::styled(" ● running ", Style::default().fg(Color::Yellow))
    } else {
        Span::styled(
            format!(" {} ", app.runner.status_text()),
            Style::default().fg(DIM),
        )
    };
    let lines = vec![
        Line::from(vec![running, Span::raw(app.status.clone())]),
        Line::from(Span::styled(app.hints(), Style::default().fg(DIM))),
    ];
    frame.render_widget(
        Paragraph::new(lines).block(Block::default().borders(Borders::ALL)),
        area,
    );
}

// ------------------------------------------------------------------ commands

fn draw_commands(frame: &mut Frame, app: &mut App, area: Rect) {
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(area);

    let indices = app.filtered();
    let items: Vec<ListItem> = indices
        .iter()
        .enumerate()
        .map(|(row, index)| {
            let spec = &COMMANDS[*index];
            let style = if row == app.commands.cursor {
                Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };
            ListItem::new(Line::from(vec![
                Span::styled(format!("{:<22}", spec.name), style),
                Span::styled(spec.help, Style::default().fg(DIM)),
            ]))
        })
        .collect();
    let title = if app.commands.filtering || !app.commands.filter.is_empty() {
        format!(" commands · filter: {} ", app.commands.filter)
    } else {
        format!(" commands ({}) ", indices.len())
    };
    frame.render_widget(
        List::new(items).block(list_block(title, app.commands.focus == Focus::List)),
        columns[0],
    );

    draw_form(frame, app, columns[1]);
}

fn draw_form(frame: &mut Frame, app: &App, area: Rect) {
    let form = &app.commands.form;
    let spec = form.spec();
    let focused = app.commands.focus == Focus::Form;
    let mut lines = vec![
        Line::from(Span::styled(spec.help, Style::default().fg(DIM))),
        Line::raw(""),
    ];
    if spec.fields.is_empty() {
        lines.push(Line::from(Span::styled(
            "no options",
            Style::default().fg(DIM),
        )));
    }
    for (index, (field, value)) in spec.fields.iter().zip(&form.values).enumerate() {
        let active = focused && index == form.cursor;
        let marker = if active { "›" } else { " " };
        let shown = match field.kind {
            Kind::Text if value.is_empty() => "—".to_string(),
            Kind::Text if active => format!("{value}▏"),
            _ => value.clone(),
        };
        let label = format!("{marker} {:<14}", field.label);
        let style = if active {
            Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        lines.push(Line::from(vec![
            Span::styled(label, style),
            Span::raw(shown),
            Span::styled(
                if field.required { "  (required)" } else { "" },
                Style::default().fg(Color::Yellow),
            ),
        ]));
        if active {
            lines.push(Line::from(Span::styled(
                format!("    {}", field.help),
                Style::default().fg(DIM),
            )));
        }
    }
    lines.push(Line::raw(""));
    if spec.profiled && !app.overrides.is_empty() {
        lines.push(Line::from(Span::styled(
            format!("{} temporary override(s) applied", app.overrides.len()),
            Style::default().fg(Color::Yellow),
        )));
    }
    lines.push(Line::from(Span::styled(
        cli::display_command(&app.form_args()),
        Style::default().fg(Color::Green),
    )));

    frame.render_widget(
        Paragraph::new(lines)
            .wrap(Wrap { trim: false })
            .block(list_block(format!(" {} ", spec.name), focused)),
        area,
    );
}

// -------------------------------------------------------------------- config

fn draw_config(frame: &mut Frame, app: &mut App, area: Rect) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(6)])
        .split(area);

    match app.config.as_ref() {
        None => frame.render_widget(
            Paragraph::new("no config loaded — press r to resolve, p to pick a profile")
                .block(list_block(" config ".to_string(), true)),
            rows[0],
        ),
        Some(tree) => {
            let height = rows[0].height.saturating_sub(2) as usize;
            let offset = scroll_offset(tree.cursor, tree.rows.len(), height);
            let items: Vec<ListItem> = tree
                .rows
                .iter()
                .enumerate()
                .skip(offset)
                .take(height.max(1))
                .map(|(index, row)| {
                    let marker = match row.expanded {
                        Some(true) => "▾ ",
                        Some(false) => "▸ ",
                        None => "  ",
                    };
                    let overridden = app.overrides.iter().any(|(path, _)| *path == row.path);
                    let name_style = if index == tree.cursor {
                        Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
                    } else {
                        Style::default()
                    };
                    let value_style = if overridden {
                        Style::default().fg(Color::Yellow)
                    } else {
                        Style::default().fg(Color::Gray)
                    };
                    ListItem::new(Line::from(vec![
                        Span::raw("  ".repeat(row.depth)),
                        Span::raw(marker),
                        Span::styled(row.label.clone(), name_style),
                        Span::raw(if row.is_leaf() { ": " } else { "" }),
                        Span::styled(row.value.clone(), value_style),
                        Span::styled(
                            if overridden { "  *" } else { "" },
                            Style::default().fg(Color::Yellow),
                        ),
                    ]))
                })
                .collect();
            frame.render_widget(
                List::new(items).block(list_block(
                    format!(" {} · config_hash {} ", tree.profile, tree.hash),
                    true,
                )),
                rows[0],
            );
        }
    }

    let overrides = if app.overrides.is_empty() {
        vec![Line::from(Span::styled(
            "none — press e on a value to set one",
            Style::default().fg(DIM),
        ))]
    } else {
        app.overrides
            .iter()
            .map(|(path, value)| {
                Line::from(Span::styled(
                    format!("{path} = {value}"),
                    Style::default().fg(Color::Yellow),
                ))
            })
            .collect()
    };
    frame.render_widget(
        Paragraph::new(overrides).block(list_block(
            format!(" temporary overrides ({}) ", app.overrides.len()),
            false,
        )),
        rows[1],
    );
}

// ------------------------------------------------------------------- promote

fn draw_promote(frame: &mut Frame, app: &mut App, area: Rect) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(4)])
        .split(area);

    let state = &app.promote;
    if state.changes.is_empty() {
        frame.render_widget(
            Paragraph::new(format!(
                "{} and {} agree on every value (or the diff is not loaded).\n\
                 s changes the source profile, r reloads.",
                state.source,
                promote::PAPER_PROFILE
            ))
            .wrap(Wrap { trim: false })
            .block(list_block(" promotion ".to_string(), true)),
            rows[0],
        );
    } else {
        let height = rows[0].height.saturating_sub(2) as usize;
        let offset = scroll_offset(state.cursor, state.changes.len(), height);
        let items: Vec<ListItem> = state
            .changes
            .iter()
            .zip(&state.selected)
            .enumerate()
            .skip(offset)
            .take(height.max(1))
            .map(|(index, (change, selected))| {
                let style = if index == state.cursor {
                    Style::default().fg(ACCENT).add_modifier(Modifier::BOLD)
                } else {
                    Style::default()
                };
                ListItem::new(Line::from(vec![
                    Span::raw(if *selected { "[x] " } else { "[ ] " }),
                    Span::styled(change.path.clone(), style),
                    Span::raw("  "),
                    Span::styled(change.old_text(), Style::default().fg(Color::Red)),
                    Span::raw(" → "),
                    Span::styled(change.new_text(), Style::default().fg(Color::Green)),
                ]))
            })
            .collect();
        frame.render_widget(
            List::new(items).block(list_block(
                format!(
                    " {} → {} · {} selected ",
                    state.source,
                    promote::PAPER_PROFILE,
                    state.selected.iter().filter(|flag| **flag).count()
                ),
                true,
            )),
            rows[0],
        );
    }

    let promoted = Paragraph::new(vec![
        Line::from(format!(
            "generated layer: {} ({} value(s) already promoted)",
            promote::PROMOTED_LAYER,
            state.promoted.len()
        )),
        Line::from(Span::styled(
            "Enter validates the full paper config before writing; nothing is written when invalid.",
            Style::default().fg(DIM),
        )),
    ])
    .wrap(Wrap { trim: false })
    .block(list_block(" target ".to_string(), false));
    frame.render_widget(promoted, rows[1]);
}

// -------------------------------------------------------------------- output

fn draw_output(frame: &mut Frame, app: &mut App, area: Rect) {
    let viewport = area.height.saturating_sub(2) as usize;
    app.output_viewport = viewport.max(1);
    let lines: Vec<Line> = app
        .runner
        .visible(app.output_viewport)
        .iter()
        .take(app.output_viewport)
        .map(|line| {
            let style = if line.starts_with("! ") {
                Style::default().fg(Color::Red)
            } else if line.starts_with('$') || line.starts_with('[') {
                Style::default().fg(DIM)
            } else {
                Style::default()
            };
            Line::from(Span::styled(line.clone(), style))
        })
        .collect();
    let title = if app.runner.command.is_empty() {
        " output · no command run yet ".to_string()
    } else {
        let follow = if app.runner.scroll.is_some() {
            " · scrolled (End to follow)"
        } else {
            ""
        };
        format!(
            " {} · {}{follow} ",
            app.runner.command,
            app.runner.status_text()
        )
    };
    let border = match app.runner.status {
        Status::Failed(_) => Color::Red,
        Status::Succeeded => Color::Green,
        _ => Color::White,
    };
    frame.render_widget(
        Paragraph::new(lines).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(border))
                .title(title),
        ),
        area,
    );
}

// --------------------------------------------------------------------- bits

fn list_block(title: String, focused: bool) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(if focused { ACCENT } else { DIM }))
        .title(title)
}

/// Keep the cursor inside the visible window of a list.
pub fn scroll_offset(cursor: usize, len: usize, height: usize) -> usize {
    if height == 0 || len <= height {
        return 0;
    }
    cursor.saturating_sub(height / 2).min(len - height)
}

fn draw_modal(frame: &mut Frame, title: &str, body: &str, footer: Option<&str>) {
    let area = centered(frame.area(), 70, 60);
    frame.render_widget(Clear, area);
    let mut lines: Vec<Line> = body
        .lines()
        .map(|line| Line::raw(line.to_string()))
        .collect();
    if let Some(footer) = footer {
        lines.push(Line::raw(""));
        lines.push(Line::from(Span::styled(
            footer.to_string(),
            Style::default().fg(Color::Yellow),
        )));
    }
    frame.render_widget(
        Paragraph::new(lines).wrap(Wrap { trim: false }).block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(ACCENT))
                .title(format!(" {title} ")),
        ),
        area,
    );
}

fn centered(area: Rect, percent_x: u16, percent_y: u16) -> Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(vertical[1])[1]
}

fn help_text() -> String {
    format!(
        "Everything runs through `uv run carcharoth ...` from the repo root.\n\
         \n\
         Tab / Shift-Tab   switch screens          Ctrl-C  quit\n\
         F1                this help              q       quit (outside text input)\n\
         \n\
         Commands  ↑↓ pick, / filter, Enter opens the form; in the form ↑↓ moves\n\
         between fields, ←→/space toggles flags and choices, typing edits text,\n\
         Enter runs (dangerous commands ask first).\n\
         \n\
         Config    browse the resolved profile; Enter expands a section or edits a\n\
         value. Edits become temporary --set overrides (validated by the CLI) and\n\
         are applied to every profiled command. d drops one, D drops all.\n\
         \n\
         Promote   diffs the source profile (plus overrides) against {}; space\n\
         selects values, Enter validates and writes them to {}.\n\
         \n\
         Output    one command at a time; ↑↓/PgUp/PgDn scroll, End follows the\n\
         tail, x cancels the running child.",
        promote::PAPER_PROFILE,
        promote::PROMOTED_LAYER
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::{Focus, Modal};
    use crate::config_tree::{Change, Tree};
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    /// Render one frame and return the visible text, line by line.
    fn screen(app: &mut App) -> String {
        let mut terminal = Terminal::new(TestBackend::new(120, 40)).unwrap();
        terminal.draw(|frame| draw(frame, app)).unwrap();
        let buffer = terminal.backend().buffer().clone();
        (0..buffer.area.height)
            .map(|y| {
                (0..buffer.area.width)
                    .map(|x| buffer[(x, y)].symbol())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn commands_screen_lists_the_catalog_and_previews_the_command() {
        let mut app = App::default();
        let text = screen(&mut app);
        assert!(text.contains("Commands"));
        assert!(text.contains("run backtest"));
        assert!(text.contains("cache clear"));
        assert!(text.contains("uv run carcharoth run -p trading/paper"));
    }

    #[test]
    fn form_shows_the_focused_field_and_pending_overrides() {
        let mut app = App {
            overrides: vec![("risk.max_open_positions".to_string(), "12".to_string())],
            ..App::default()
        };
        app.commands.form =
            crate::catalog::Form::new(crate::catalog::index_by_name("run backtest").unwrap());
        app.commands.focus = Focus::Form;
        app.commands.form.cursor = 1;
        let text = screen(&mut app);
        assert!(text.contains("start date"));
        assert!(text.contains("YYYY-MM-DD; overrides data.start"));
        assert!(text.contains("1 temporary override(s) applied"));
        assert!(text.contains("--set risk.max_open_positions=12"));
    }

    #[test]
    fn config_screen_shows_the_tree_and_marks_overridden_values() {
        let mut app = App {
            screen: Screen::Config,
            overrides: vec![("symbols".to_string(), "[SPY]".to_string())],
            config: Some(Tree::new(
                "backtest".to_string(),
                serde_json::json!({"symbols": ["SPY"], "risk": {"max_open_positions": 8}}),
                "abc123".to_string(),
            )),
            ..App::default()
        };
        let text = screen(&mut app);
        assert!(text.contains("backtest · config_hash abc123"));
        assert!(text.contains("symbols: [\"SPY\"]"));
        assert!(
            text.contains("▸ risk"),
            "objects render as collapsible branches"
        );
        assert!(text.contains("temporary overrides (1)"));
        assert!(text.contains("symbols = [SPY]"));
    }

    #[test]
    fn promote_screen_shows_selection_and_the_generated_target() {
        let mut app = App {
            screen: Screen::Promote,
            ..App::default()
        };
        app.promote.loaded = true;
        app.promote.changes = vec![Change {
            path: "risk.max_open_positions".to_string(),
            from: Some(serde_json::json!(5)),
            to: serde_json::json!(8),
        }];
        app.promote.selected = vec![true];
        let text = screen(&mut app);
        assert!(text.contains("[x] risk.max_open_positions"));
        assert!(text.contains("5 → 8"));
        assert!(text.contains("backtest → trading/paper · 1 selected"));
        assert!(text.contains(promote::PROMOTED_LAYER));
    }

    #[test]
    fn output_screen_shows_the_command_status_and_lines() {
        let mut app = App {
            screen: Screen::Output,
            ..App::default()
        };
        app.runner.command = "uv run carcharoth config list".to_string();
        app.runner.status = Status::Failed("exit code 1".to_string());
        app.runner.lines = vec![
            "$ uv run carcharoth config list".to_string(),
            "! boom".to_string(),
        ];
        let text = screen(&mut app);
        assert!(text.contains("failed: exit code 1"));
        assert!(text.contains("! boom"));
        assert!(
            app.output_viewport > 0,
            "the viewport is recorded for scrolling"
        );
    }

    #[test]
    fn modals_render_over_the_screen() {
        let mut app = App {
            modal: Modal::Help,
            ..App::default()
        };
        assert!(screen(&mut app).contains("Tab / Shift-Tab"));

        app.modal = Modal::Input {
            prompt: crate::app::Prompt::EditLeaf {
                path: "risk.max_open_positions".to_string(),
            },
            label: "risk.max_open_positions".to_string(),
            buffer: "12".to_string(),
            error: Some("nope".to_string()),
        };
        let text = screen(&mut app);
        assert!(text.contains("> 12"));
        assert!(text.contains("nope"));
    }

    #[test]
    fn short_lists_are_never_scrolled() {
        assert_eq!(scroll_offset(3, 5, 10), 0);
        assert_eq!(scroll_offset(0, 0, 10), 0);
    }

    #[test]
    fn cursor_stays_visible_in_long_lists() {
        assert_eq!(scroll_offset(0, 100, 10), 0);
        assert_eq!(scroll_offset(50, 100, 10), 45);
        assert_eq!(scroll_offset(99, 100, 10), 90);
    }
}
