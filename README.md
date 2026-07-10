# Kudos Bot

A Slack peer-recognition system where employees publicly appreciate each other's work. Kudos accumulate as points that convert to real dollar payouts, with the conversion rate and monthly budget controlled by accounting.

## Design Principles

**Frictionless giving.** One Slack message: `@kudos-bot @jane Great job leading the incident retro today!`

**Structurally hard to game.** Anti-abuse is enforced at the database level (CHECK constraints, advisory locks, rate limits), not application code. An LLM gates every kudos for substantive content — vague praise is rejected.

**Reciprocity.** Points only convert to dollars when your given count matches your received count. You earn payouts by recognizing others, not just by being recognized.

**Budget control.** Accounting sets a monthly point budget and conversion rate. Exhausted budgets queue payouts FIFO rather than rejecting them.

**Measurable.** A dashboard uses interrupted time series analysis to estimate the causal effect of conversion-rate changes on activity.

## Architecture

All business logic lives in Postgres functions. The Python app is a thin event router.

```
app.py                  Slack event handlers (mentions, edits, deletes, channel joins)
dash_app.py             Plotly Dash dashboard (usage, IRR, leaderboard, topics)
cron/
  overflow.py           Monthly: process queued redemptions against new budget
  weekly_reminder.py    Weekly: DM users who haven't given kudos
  backfill.py           Weekly: embed kudos messages, cluster, LLM-summarize topics
  record_users.py       Weekly: record covariates (num_users, workday_frac, channel_messages)
schema/
  schema.sql            Tables, indexes, CHECK constraints
  views_and_functions.sql  Views and PL/pgSQL functions (give_kudos, try_redeem, etc.)
systemd/                Systemd timers and services
tests/
  test_services.py      Database logic tests (give, redeem, limits, overflow, reminders)
  test_dashboard.py     Playwright tests for all dashboard panels
simulate.py             Synthetic data generator for demo/testing
```

### Schema

| Table | Purpose |
|---|---|
| `kudos` | Every kudos event (giver, recipient, message, embedding, timestamps) |
| `users` | Slack user ID → display name |
| `budgets` | Monthly point budget and conversion rate |
| `covariates` | Weekly time-varying covariates keyed by `(label, week)` — `num_users`, `workday_frac`, `channel_messages` |
| `clusters` | KMeans cluster centers with LLM-generated summary labels |
| `cluster_members` | Kudos → cluster membership |

### Slack Events

| Event | Handler |
|---|---|
| `app_mention` | Give kudos: validate content (LLM), enforce limits, insert, attempt redemption |
| `message_changed` | Re-evaluate edited kudos (delete old, re-give) |
| `message_deleted` | Soft-delete kudos; warn accounting if already redeemed |
| `member_joined_channel` | Onboarding message in public channels; leave private channels |

### Database Functions

| Function | Purpose |
|---|---|
| `give_kudos()` | Validate limits, insert kudos, attempt auto-redemption |
| `try_redeem()` | Budget lock, FIFO payout, budget-exhaustion detection |
| `check_kudos_limits()` | Per-user advisory lock, daily cap, monthly per-pair cap |
| `delete_kudos()` | Soft-delete with redeemed-point audit |

### Dashboard

The Dash app (`dash_app.py`) provides:

- **Operational snapshot** — current budget, spent this month, queued count
- **Usage & budget** — weekly acquired/redeemed bars with budget line and Poisson forecast
- **Treatment effect** — IRR plot from a Poisson GLM with successive difference contrasts, adjusted for time-varying covariates (`num_users`, `workday_frac`, `channel_messages`) when they vary
- **Leaderboard** — points received per person
- **Topic evolution** — streamgraph of LLM-labeled topic clusters with drill-down to messages

## Setup

### Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- PostgreSQL with [pgvector](https://github.com/pgvector/pgvector) extension
- [pg-schema-diff](https://github.com/stripe/pg-schema-diff) for migrations
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
| `DASH_PROD` | No | Set to disable Dash debug mode |

### Running

```sh
# Apply schema
pg-schema-diff apply --from-dsn "$DATABASE_URL" --to-dir schema \
  --allow-hazards HAS_UNTRACKABLE_DEPENDENCIES

# Start the bot
uv run python app.py

# Start the dashboard
uv run python dash_app.py
```

### Testing

```sh
createdb kudos_test
uv run pytest tests/test_services.py -x -s
uv run pytest tests/test_dashboard.py -x -s
```

Dashboard tests use Dash's built-in `dash_duo` fixture and require LLM servers running (`CHAT_URI`, `EMBEDDING_URI`) for the `simulate.py` data seed.

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

**Generate the IRR plot** (requires `DATABASE_URL` and data in `weekly_kudos`):

```sh
uv run presentation/irr_plot.py
```

**Build the PDF:**

```sh
bash presentation/publish.sh
```

This produces `presentation/slideshow.pdf`.
