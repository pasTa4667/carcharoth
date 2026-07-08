#!/usr/bin/env bash
# Create or update the grafana_reader Postgres role to match GRAFANA_DB_PASSWORD in .env.
# Safe to re-run. Does not restart any containers.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "error: .env not found in $ROOT" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ -z "${GRAFANA_DB_PASSWORD:-}" ]]; then
  echo "error: GRAFANA_DB_PASSWORD is not set in .env" >&2
  exit 1
fi

escaped="${GRAFANA_DB_PASSWORD//\'/\'\'}" 

docker compose exec -T db psql -U carcharoth -d carcharoth <<EOF
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_reader') THEN
    CREATE USER grafana_reader WITH PASSWORD '${escaped}';
  ELSE
    ALTER USER grafana_reader WITH PASSWORD '${escaped}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE carcharoth TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO grafana_reader;
EOF

echo "grafana_reader user synced."
