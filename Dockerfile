FROM python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git golang-go systemd ca-certificates \
    postgresql postgresql-common && \
    rm -rf /var/lib/apt/lists/*

# Install pgvector
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-16-pgvector && \
    rm -rf /var/lib/apt/lists/*

# Install patched pg-schema-diff (PR #286: function/view dependency ordering)
RUN git clone --depth 1 --branch function-view-deps \
    https://github.com/da77a/pg-schema-diff.git /tmp/pg-schema-diff && \
    cd /tmp/pg-schema-diff && \
    go build -o /usr/local/bin/pg-schema-diff ./cmd/pg-schema-diff && \
    rm -rf /tmp/pg-schema-diff /root/go

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /opt/kudos

# Install dependencies (cached unless pyproject.toml/lock changes)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

# App code
COPY . .

# Systemd units + database initialization
RUN cp systemd/*.service systemd/*.timer /etc/systemd/system/ && \
    systemctl enable postgresql && \
    service postgresql start && \
    su postgres -c "createdb kudos" && \
    DATABASE_URL=postgresql://postgres@localhost/kudos scripts/migrate.sh && \
    DATABASE_URL=postgresql://postgres@localhost/kudos psql -f scripts/setup.sql && \
    service postgresql stop && \
    systemctl enable kudos-bot kudos-dashboard \
    kudos-backfill.timer kudos-weekly-reminder.timer kudos-accounting.timer

EXPOSE 8050

CMD ["/sbin/init"]
