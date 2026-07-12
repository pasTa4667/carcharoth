"""Post-run metrics, analyzer and objective (fitness) scoring.

Import submodules directly (e.g. ``carcharoth.analysis.analyzer``); the
package init stays empty so that ``persistence.repositories`` can import
``analysis.metrics`` without pulling in the analyzer, which imports
repositories back (circular otherwise).
"""
