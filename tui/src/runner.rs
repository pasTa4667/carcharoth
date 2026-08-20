//! Running one CLI command at a time and collecting its output.
//!
//! The child is spawned without a shell, its stdout and stderr are streamed
//! line by line through a channel into a bounded buffer, and it can be
//! cancelled (or killed on shutdown) at any point.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, Sender, TryRecvError};
use std::thread;

use anyhow::{anyhow, Result};

use crate::cli;

/// Lines older than this are dropped, so a long backtest cannot grow the
/// buffer without bound.
const MAX_LINES: usize = 5_000;

enum Message {
    Line(String),
    Eof,
}

/// One in-flight child process.
struct Running {
    child: Child,
    rx: Receiver<Message>,
    /// stdout + stderr readers still to report EOF
    open_streams: usize,
}

/// How the last command ended.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Status {
    Idle,
    Running,
    Succeeded,
    Failed(String),
    Cancelled,
}

/// The output pane's state: the command being shown, its lines, scrolling,
/// and the process behind it.
pub struct Runner {
    running: Option<Running>,
    pub command: String,
    pub lines: Vec<String>,
    pub status: Status,
    /// first visible line; `None` follows the tail
    pub scroll: Option<usize>,
}

impl Default for Runner {
    fn default() -> Self {
        Runner {
            running: None,
            command: String::new(),
            lines: Vec::new(),
            status: Status::Idle,
            scroll: None,
        }
    }
}

impl Runner {
    pub fn is_running(&self) -> bool {
        self.running.is_some()
    }

    /// Spawn `uv run carcharoth <args...>`. Refuses while a command is
    /// still running — one child at a time keeps the DB and logs sane.
    pub fn start(&mut self, args: &[String]) -> Result<()> {
        if self.is_running() {
            return Err(anyhow!(
                "a command is already running (press x to cancel it)"
            ));
        }
        let mut child = Command::new(cli::LAUNCHER)
            .args(cli::argv(args))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|err| anyhow!("failed to start `{}`: {err}", cli::LAUNCHER))?;

        let (tx, rx) = channel();
        let stdout = child.stdout.take().expect("stdout piped");
        let stderr = child.stderr.take().expect("stderr piped");
        pump(stdout, tx.clone(), "");
        pump(stderr, tx, "! ");

        self.command = cli::display_command(args);
        self.lines = vec![format!("$ {}", self.command)];
        self.status = Status::Running;
        self.scroll = None;
        self.running = Some(Running {
            child,
            rx,
            open_streams: 2,
        });
        Ok(())
    }

    /// Drain everything the child produced since the last call and reap it
    /// once both streams closed. Returns true when the view changed.
    pub fn poll(&mut self) -> bool {
        let mut new_lines = Vec::new();
        let mut exit = None;
        {
            let Some(running) = self.running.as_mut() else {
                return false;
            };
            loop {
                match running.rx.try_recv() {
                    Ok(Message::Line(line)) => new_lines.push(line),
                    Ok(Message::Eof) => running.open_streams -= 1,
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        running.open_streams = 0;
                        break;
                    }
                }
            }
            if running.open_streams == 0 {
                exit = running.child.try_wait().ok().flatten();
            }
        }
        let mut changed = !new_lines.is_empty();
        self.push_lines(new_lines);
        if let Some(status) = exit {
            self.running = None;
            if self.status != Status::Cancelled {
                self.status = if status.success() {
                    Status::Succeeded
                } else {
                    Status::Failed(match status.code() {
                        Some(code) => format!("exit code {code}"),
                        None => "terminated by signal".to_string(),
                    })
                };
            }
            self.push_lines(vec![format!("[{}]", self.status_text())]);
            changed = true;
        }
        changed
    }

    /// Ask the child to stop; the exit is reported by the next [`poll`].
    pub fn cancel(&mut self) -> Result<()> {
        let Some(running) = self.running.as_mut() else {
            return Err(anyhow!("no command is running"));
        };
        running
            .child
            .kill()
            .map_err(|err| anyhow!("could not cancel: {err}"))?;
        self.status = Status::Cancelled;
        Ok(())
    }

    /// Kill any child on shutdown so quitting never leaves an orphan.
    pub fn shutdown(&mut self) {
        if let Some(mut running) = self.running.take() {
            let _ = running.child.kill();
            let _ = running.child.wait();
        }
    }

    pub fn status_text(&self) -> String {
        match &self.status {
            Status::Idle => "idle".to_string(),
            Status::Running => "running".to_string(),
            Status::Succeeded => "done".to_string(),
            Status::Failed(reason) => format!("failed: {reason}"),
            Status::Cancelled => "cancelled".to_string(),
        }
    }

    fn push_lines(&mut self, lines: Vec<String>) {
        if lines.is_empty() {
            return;
        }
        self.lines.extend(lines);
        if self.lines.len() > MAX_LINES {
            let excess = self.lines.len() - MAX_LINES;
            self.lines.drain(..excess);
            if let Some(scroll) = self.scroll.as_mut() {
                *scroll = scroll.saturating_sub(excess);
            }
        }
    }

    /// Scroll by `delta` lines; scrolling to the bottom re-enables following.
    pub fn scroll_by(&mut self, delta: isize, viewport: usize) {
        let max_top = self.lines.len().saturating_sub(viewport.max(1));
        let current = self.scroll.unwrap_or(max_top) as isize;
        let next = (current + delta).clamp(0, max_top as isize) as usize;
        self.scroll = if next >= max_top { None } else { Some(next) };
    }

    /// The window of lines to render, given the pane height.
    pub fn visible(&self, viewport: usize) -> &[String] {
        let max_top = self.lines.len().saturating_sub(viewport.max(1));
        let top = self.scroll.unwrap_or(max_top).min(max_top);
        &self.lines[top..]
    }
}

/// Forward one stream's lines into the channel, marking EOF when it closes.
fn pump<R: std::io::Read + Send + 'static>(stream: R, tx: Sender<Message>, prefix: &'static str) {
    thread::spawn(move || {
        for line in BufReader::new(stream).lines() {
            let line = match line {
                Ok(line) => line,
                Err(err) => format!("<read error: {err}>"),
            };
            if tx.send(Message::Line(format!("{prefix}{line}"))).is_err() {
                return;
            }
        }
        let _ = tx.send(Message::Eof);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn runner_with(count: usize) -> Runner {
        Runner {
            lines: (0..count).map(|i| format!("line {i}")).collect(),
            ..Runner::default()
        }
    }

    #[test]
    fn buffer_is_bounded_and_keeps_the_newest_lines() {
        let mut runner = Runner::default();
        runner.push_lines((0..MAX_LINES + 10).map(|i| i.to_string()).collect());
        assert_eq!(runner.lines.len(), MAX_LINES);
        assert_eq!(runner.lines.last().unwrap(), &(MAX_LINES + 9).to_string());
    }

    #[test]
    fn scrolling_up_pins_the_view_and_bottom_follows_again() {
        let mut runner = runner_with(100);
        runner.scroll_by(-10, 20);
        assert_eq!(runner.scroll, Some(70));
        assert_eq!(runner.visible(20).first().unwrap(), "line 70");
        runner.scroll_by(100, 20);
        assert_eq!(runner.scroll, None); // following the tail again
    }

    #[test]
    fn scrolling_cannot_leave_the_buffer() {
        let mut runner = runner_with(10);
        runner.scroll_by(-1000, 20);
        assert_eq!(runner.scroll, None); // fewer lines than the viewport
        let mut runner = runner_with(100);
        runner.scroll_by(-1000, 20);
        assert_eq!(runner.scroll, Some(0));
    }

    #[test]
    fn status_text_explains_failures() {
        let mut runner = Runner::default();
        assert_eq!(runner.status_text(), "idle");
        runner.status = Status::Failed("exit code 1".to_string());
        assert_eq!(runner.status_text(), "failed: exit code 1");
    }

    #[test]
    fn cancelling_without_a_child_is_an_error() {
        assert!(Runner::default().cancel().is_err());
    }
}
