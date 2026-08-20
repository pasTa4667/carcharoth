//! Carcharoth TUI: a thin terminal front-end for `uv run carcharoth ...`.
//!
//! Run it from the repository root — the Python CLI resolves `config/` and
//! `.env` relative to the working directory.

use std::io::{self, Stdout};
use std::panic;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::cursor::Show;
use crossterm::event::{self, Event};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use carcharoth_tui::app::App;
use carcharoth_tui::ui;

/// Poll interval: short enough for smooth command output, cheap when idle.
const TICK: Duration = Duration::from_millis(100);

fn main() -> Result<()> {
    if !std::path::Path::new("config/base.yaml").is_file() {
        eprintln!(
            "carcharoth-tui must be started from the repository root (config/base.yaml not found)"
        );
        std::process::exit(2);
    }
    install_panic_hook();
    let mut terminal = setup().context("could not initialize the terminal")?;
    let result = run(&mut terminal);
    restore()?;
    result
}

fn run(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> Result<()> {
    let mut app = App::default();
    loop {
        app.refresh();
        terminal.draw(|frame| ui::draw(frame, &mut app))?;
        if event::poll(TICK)? {
            match event::read()? {
                Event::Key(key) if key.kind == event::KeyEventKind::Press => app.handle_key(key),
                _ => {}
            }
        }
        // Child output is drained every tick, so a running command streams
        // into the output pane no matter which screen is visible.
        app.runner.poll();
        if app.should_quit {
            app.runner.shutdown();
            return Ok(());
        }
    }
}

fn setup() -> Result<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    Ok(Terminal::new(CrosstermBackend::new(stdout))?)
}

fn restore() -> Result<()> {
    execute!(io::stdout(), LeaveAlternateScreen, Show)?;
    disable_raw_mode()?;
    Ok(())
}

/// A panic must not leave the terminal in raw mode / the alternate screen.
fn install_panic_hook() {
    let hook = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        let _ = restore();
        hook(info);
    }));
}
