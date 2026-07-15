# Operations

## Running the App

```bash
# Start dependencies (Postgres + Grafana + Redis)
docker compose up -d

# Run migrations
uv run alembic upgrade head

# Run the bot (ticks every minute during market hours)
uv run python -m carcharoth        # equivalent: uv run carcharoth run
```

The bot shuts down cleanly on Ctrl-C or SIGTERM.

### Docker
```bash
docker build -t carcharoth .
docker run --env-file .env carcharoth
```

## Monitoring

- **Grafana**: `http://localhost:3333` (see `.env` for credentials) — dashboards:
  *Trading Overview* (live, PAPER runs only), *Live Analysis* (equity/drawdown per paper
  run), *Backtest Results* (metrics + equity curve per backtest run)
- **Logs**: `logs/` directory
- **Database**: `docker compose exec db psql -U carcharoth -d carcharoth -c "\dt"`

## Troubleshooting

**"No such table" error:** Run `uv run alembic upgrade head`

**Grafana shows no data:** Check Postgres connection under **Connections → Data sources**,
then restart: `docker compose restart grafana`

**"redis unreachable" warning:** Run `docker compose up -d redis` — backtests still work
without it, just uncached

**Suspicious backtest results after changing data feed / bumping hmmlearn:** Run
`uv run carcharoth cache clear` to drop stale cached bars/fits

**Type errors:** Run `uv run mypy src` to see full type-checking report

**Lint/format issues:** Run `uv run ruff check --fix && uv run ruff format`
