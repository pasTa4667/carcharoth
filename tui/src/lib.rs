//! Carcharoth TUI internals.
//!
//! The binary in `main.rs` owns the terminal and the event loop; everything
//! else lives here so it can be unit- and integration-tested. Nothing in
//! this crate reimplements trading or config logic: the Python CLI stays the
//! single source of truth (see [`cli`]).

pub mod app;
pub mod catalog;
pub mod cli;
pub mod config_tree;
pub mod promote;
pub mod runner;
pub mod ui;
