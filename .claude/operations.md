# Operations

## Running the App

```bash
# Start dependencies (Postgres + Grafana + Redis)
docker compose up -d db redis grafana

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
  run), *Backtest Results* (metrics + equity curve per backtest run),
  *Backtest Performance* (bounded phase/hotspot timings, CPU, throughput and checkpoint health),
  *Backtest Loops* (loop UUID/label, iteration results, independent equity and drilldowns).
  See the [performance runbook](backtest-performance.md); apply migrations before using new panels.
- **Logs**: `logs/` directory
- **Database**: `docker compose exec db psql -U carcharoth -d carcharoth -c "\dt"`

## Research loops

Run `uv run alembic upgrade head` before the new Backtest Loop tab/command; migration
`e1f2a3b4c5d6` adds nullable grouping metadata to runs. Refresh Grafana provisioning (restart
Grafana if needed) to load Backtest Loops. See the [loop runbook](backtest.md#backtest-loops).
An interrupted iteration can remain unfinished; completed results remain available. There is no
automatic resume. Do not use `finished_at` alone as proof analysis completed.

## Troubleshooting

**"No such table" error:** Run `uv run alembic upgrade head`

**Grafana shows no data:** Check Postgres connection under **Connections → Data sources**,
then restart: `docker compose restart grafana`

**"redis unreachable" warning:** Run `docker compose up -d redis` — backtests still work
without it, just uncached

**Historical bars cache:** v2 stores UTC calendar-month Arrow IPC chunks (Zstandard),
including successful empty fetched intervals. Refill is lazy; v1 pickles remain untouched
and unread. `uv run carcharoth cache stats` distinguishes v1 entries from v2 chunks;
Redis memory is server-wide, not bars-only.

`uv run carcharoth cache clear --bars-version v2` is the default cleanup.
Use `--bars-version v1` or `all` only for deliberate legacy cleanup. No pickle conversion
or fit-cache deletion occurs. `--set cache.bars=false` bypasses the bars adapter (also training).
Today's tail is never persisted; partial writes/corruption cause misses. Each value owns its
coverage: concurrent stale writers can lose hit rate and require refetch, not invent coverage.
Reads/decode are bounded to 16 monthly chunks per batch; requested output still scales with history.

**Type errors:** Run `uv run mypy src` to see full type-checking report

**Lint/format issues:** Run `uv run ruff check --fix && uv run ruff format`
