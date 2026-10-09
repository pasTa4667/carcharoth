//! Read-only first/repeat tab timing check against the real Python CLI.
//! Run with `cargo test --workspace --test tab_loading -- --ignored --nocapture`.
use std::time::Instant;

use carcharoth_tui::app::{App, Screen};
use carcharoth_tui::ui;
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::{backend::TestBackend, Terminal};

#[test]
#[ignore = "spawns the Python CLI; reports timings without hardware-dependent thresholds"]
fn first_and_repeat_tab_loads() {
    std::env::set_current_dir(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap(),
    )
    .unwrap();
    let mut first = [vec![], vec![], vec![]];
    let mut repeat = [vec![], vec![], vec![]];
    for _ in 0..5 {
        let mut app = App::default();
        let mut terminal = Terminal::new(TestBackend::new(120, 40)).unwrap();
        for (i, screen) in [Screen::Config, Screen::Promote, Screen::BacktestLoop]
            .into_iter()
            .enumerate()
        {
            let start = Instant::now();
            app.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
            assert!(app.screen == screen);
            app.refresh();
            terminal.draw(|frame| ui::draw(frame, &mut app)).unwrap();
            first[i].push(start.elapsed().as_secs_f64() * 1000.0);
            match screen {
                Screen::Config => assert!(app.config.is_some(), "{}", app.status),
                Screen::Promote => {
                    assert!(app.status.contains("value(s) differ"), "{}", app.status)
                }
                Screen::BacktestLoop => {
                    assert!(app.backtest_loop.loaded);
                    assert!(
                        app.status == "refreshed local artifacts; Enter previews the schedule",
                        "{}",
                        app.status
                    );
                }
                _ => unreachable!(),
            }
            // Leave and revisit the tab: retained data must avoid a second CLI load.
            app.handle_key(KeyEvent::new(KeyCode::BackTab, KeyModifiers::NONE));
            let start = Instant::now();
            app.handle_key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE));
            app.refresh();
            terminal.draw(|frame| ui::draw(frame, &mut app)).unwrap();
            repeat[i].push(start.elapsed().as_secs_f64() * 1000.0);
        }
    }
    for (i, name) in ["Config", "Promote", "Backtest Loop"]
        .into_iter()
        .enumerate()
    {
        first[i].sort_by(f64::total_cmp);
        repeat[i].sort_by(f64::total_cmp);
        println!(
            "{name}: first median={:.1}ms worst={:.1}ms; repeat median={:.2}ms worst={:.2}ms",
            first[i][2], first[i][4], repeat[i][2], repeat[i][4]
        );
    }
}
