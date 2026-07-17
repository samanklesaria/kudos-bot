# Kudos Bot

A Slack peer-recognition system where employees publicly appreciate each other's work. Kudos accumulate as points that convert to real dollar payouts, with the conversion rate and monthly budget controlled by accounting.

## Design Principles

**Frictionless giving.** One Slack message: `@kudos @jane Great job leading the incident retro today!`

**Structurally hard to game.** Anti-abuse is enforced at the database level (CHECK constraints, advisory locks, rate limits), not application code. An LLM gates every kudos for substantive content — vague praise is rejected.

**Reciprocity.** Points only convert to dollars when your given count matches your received count. You earn payouts by recognizing others, not just by being recognized.

**Budget control.** Accounting sets a monthly point budget and conversion rate. When the budget is exhausted, kudos are still recorded but marked as overflow — the payout opportunity is lost.

**Measurable.** A dashboard uses interrupted time series analysis to estimate the causal effect of conversion-rate changes on activity.

## Architecture

All business logic lives in Postgres functions. The Python app is a thin event router.

```
app.py                  Slack event handlers (mentions, edits, deletes, channel joins)
dash_app.py             Plotly Dash dashboard (usage, IRR, leaderboard, topics)
cron/
  weekly_reminder.py    Weekly: DM users who haven't given kudos
  backfill.py           Weekly: embed kudos messages, cluster, LLM-summarize topics
  record_users.py       Weekly: record covariates (num_users, workday_frac, channel_messages)
  accounting.py         Monthly: report last month's redemptions to accounting channel
schema/
  schema.sql            Tables, indexes, CHECK constraints
  views_and_functions.sql  Views and PL/pgSQL functions (give_kudos, try_redeem, etc.)
systemd/                Systemd timers and services
tests/
  test_services.py      Database logic tests (give, redeem, limits, overflow, reminders)
  test_dashboard.py     Tests for all dashboard panels
  test_cron.py          Tests for all cron jobs
simulate.py             Synthetic data generator for demo/testing
```

### Schema

| Table | Purpose |
|---|---|
| `kudos` | Every kudos event (giver, recipient, message, embedding, timestamps, overflow flags) |
| `users` | Slack user ID → display name |
| `budgets` | Monthly point budget and conversion rate |
| `covariates` | Weekly time-varying covariates keyed by `(label, week)` — `num_users`, `workday_frac` |
| `clusters` | KMeans cluster centers with LLM-generated summary labels |
| `cluster_members` | Kudos → cluster membership |

### Slack Events

| Event | Handler |
|---|---|
| `app_mention` | Give kudos: validate content (LLM), enforce limits, insert, attempt giver+recipient redemption |
| `message_changed` | Re-evaluate edited kudos (delete old, re-give) |
| `message_deleted` | Delete kudos, undoing any redemptions it implied |
| `member_joined_channel` | Onboarding message in public channels; leave private channels |

### Database Functions

| Function | Purpose |
|---|---|
| `give_kudos()` | Validate limits, insert kudos, attempt auto-redemption |
| `try_redeem()` | Giver+recipient redemption or overflow marking |
| `check_kudos_limits()` | Daily + monthly per-pair cap |
| `delete_kudos()` | Hard-delete, un-redeem linked kudos, re-run redemption |

### Dashboard

The Dash app (`dash_app.py`) provides:

- **Operational snapshot** — current budget, spent this month, overflow count
- **Usage & budget** — weekly acquired/redeemed bars with budget line and Poisson forecast
- **Treatment effect** — IRR plot from a Poisson GLM with successive difference contrasts, adjusted for time-varying covariates (`num_users`, `workday_frac`) when they vary
- **Leaderboard** — points received per person
- **Topic evolution** — streamgraph of LLM-labeled topic clusters with drill-down to messages

## Setup

### Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- PostgreSQL with [pgvector](https://github.com/pgvector/pgvector) extension
- [pg-schema-diff](https://github.com/stripe/pg-schema-diff) for migrations — requires [PR #286](https://github.com/stripe/pg-schema-diff/pull/286) for correct function/view dependency ordering
- An OpenAI-compatible LLM server (e.g. llama.cpp) for content gating and embeddings

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SLACK_BOT_TOKEN` | Yes | Slack bot OAuth token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Yes | Slack app-level token (`xapp-...`) for Socket Mode |
| `CHAT_URI` | Yes | OpenAI-compatible chat completions endpoint |
| `EMBEDDING_URI` | Yes | OpenAI-compatible embeddings endpoint |
| `KUDOS_ACCOUNTING_CHANNEL` | No | Slack channel ID for budget/audit alerts |
| `DASH_DEBUG` | No | Set to enable Dash debug mode |

### Development Setup

```sh
# Install pg-schema-diff with function/view dependency fix (PR #286)
git clone --depth 1 --branch function-view-deps https://github.com/da77a/pg-schema-diff.git /tmp/pg-schema-diff
cd /tmp/pg-schema-diff && go build -o "$(go env GOPATH)/bin/pg-schema-diff" ./cmd/pg-schema-diff

# Apply schema
./scripts/migrate.sh

# Add an initial budget
psql $DATABASE_URL <scripts/setup.sql

# Start the LLMs in a separate terminal
sh scripts/run_llms.sh

# Get dependencies
uv sync --dev

# Start the bot
uv run python app.py

# OR simulate fake data and run the dashboard
uv run simulate.py
uv run python dash_app.py

# OR start the marimo diagnostics notebook 
uv run marimo edit diagnostics.md
```

### Testing

```sh
createdb kudos_test
uv run pytest tests/test_services.py -x -s
uv run pytest tests/test_dashboard.py -x -s
```

Dashboard tests use Dash's built-in `dash_duo` fixture and require LLM servers running (`CHAT_URI`, `EMBEDDING_URI`) for the `simulate.py` data seed.

### Deployment

The Dockerfile builds a self-contained systemd-based container with Postgres, the bot, dashboard, and all cron jobs.

```sh
docker build -t kudos .
docker run -d --env-file .env -p 8050:8050 kudos
```

The container runs `systemd` as PID 1, starts Postgres automatically, and enables:

| Unit | Description |
|------|-------------|
| `postgresql.service` | Postgres with pgvector (database created at build time) |
| `kudos-bot.service` | Slack bot (`app.py` via Socket Mode) |
| `kudos-dashboard.service` | Gunicorn serving the Dash app on port 8050 |
| `kudos-backfill.timer` | Weekly embedding + clustering backfill |
| `kudos-weekly-reminder.timer` | Weekly DM reminders |
| `kudos-accounting.timer` | Monthly redemption report |

View logs with `journalctl`:

```sh
docker exec -it <container> journalctl -u kudos-bot -f
docker exec -it <container> journalctl -u kudos-dashboard -f
```

### Presentation

The slideshow lives in `presentation/` and is built with [Pandoc](https://pandoc.org/) (Beamer output) and [Mermaid](https://mermaid.js.org/) diagrams.

**Dependencies:**

```sh
# Pandoc + XeLaTeX (macOS)
brew install pandoc mactex

# Mermaid CLI (used by the mermaid.lua Pandoc filter)
npm install -g @mermaid-js/mermaid-cli
```

Verify `mmdc` is on your PATH: `mmdc --version`.

**Build the PDF:**

```sh
bash presentation/publish.sh
```

This produces `presentation/slideshow.pdf`.
