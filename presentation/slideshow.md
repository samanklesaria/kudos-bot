# Kudos Bot

Peer recognition programs fail when they're hard to use or easy to game. Kudos Bot makes giving praise as simple as a Slack message — and makes abuse structurally impossible rather than policy-dependent.

# The Problem

```mermaid
graph LR
    A["Forms, portals,<br>manager approval"] -->|"Kudos Bot"| B["One Slack message"]
    C["Reciprocal<br>farming"] -->|"Reciprocity +<br>LLM gate"| D["Must give genuine<br>praise to earn"]
    E["Unknown effect of<br>budget on activity"] -->|"ITS dashboard"| F["Causal estimation<br>of spend impact"]
```

# Demo: Slack Bot

Live demo: onboarding, content check, edit-to-fix, error handling, and private channel rejection.

# Demo: Dashboard

Live demo: operational snapshot, usage & budget forecast, treatment effect plot, leaderboard, and topic drill-down.

# Design Principles

```mermaid
graph TD
    A["Simple for the giver<br>@kudos-bot @jane Great retro!"] --> B["Hard to game<br>DB constraints,<br>not app rules"]
    A --> C["Budget control<br>Monthly cap with<br>FIFO overflow"]
    A --> D["Measurable<br>Causal effect<br>estimation"]
```

# Reciprocity: You Earn by Giving

Points convert to dollars only when your given count matches your received count.

$$\text{owed} = \min(\text{given},\, \text{received}) - \text{redeemed}$$

|       | Given | Received | Redeemed | Owed |
|-------|------:|---------:|---------:|-----:|
| Alice |     5 |        3 |        2 |    1 |
| Bob   |     1 |        8 |        1 |    0 |

Bob has 7 unredeemed kudos. He earns his next payout by recognizing someone else.

# Anti-Abuse: Defense in Depth

```mermaid
graph LR
    subgraph Database
        L1["CHECK constraint<br>no self-kudos"]
        L2["Advisory lock<br>per-user serialization"]
        L3["Rate limits<br>1/day, 1/recipient/month"]
        L4["UNIQUE index<br>idempotent inserts"]
    end
    subgraph Application
        L5["LLM content gate<br>YES/NO, max_tokens=5"]
        L6["Public channels only<br>no shadow networks"]
    end
```

Every layer is enforced at the database or infrastructure level — not application code that can be bypassed.

# Budget Control

Accounting sets a monthly point budget and conversion rate. When the budget is exhausted, payouts queue FIFO rather than being rejected. Queued claims draw from the *next* month's budget first, before new claims.

```mermaid
graph LR
    M1["Month 1<br>budget=100<br>redeemed=100"] -- "12 queued" --> M2["Month 2<br>budget=100"]
    M2 -- "first 12" --> Q["Queued claims<br>FIFO"]
    M2 -- "remaining 88" --> N["New claims"]
```

Accounting is notified on the first rollover. A dashboard tracks queue depth over time so chronic underbudgeting is visible.

# Architecture

All business logic lives in Postgres functions. The Python app is a 131-line event router.

```mermaid
graph LR
    S["Slack event"] --> A["app.py<br>131 lines"]
    A --> GK["give_kudos()"]
    GK --> CL["check_kudos_limits()<br>advisory lock"]
    GK --> TR["try_redeem()<br>budget lock, FIFO"]
    TR --> A
    A --> Reply["Post reply"]
```

Edits delete the old point and re-evaluate from scratch. Deletions remove the point; if already redeemed, accounting is warned.

# Scheduled Jobs

```mermaid
graph LR
    O["overflow.py<br><i>monthly</i>"] --> B["Process queued<br>redemptions against<br>new budget"]
    W["weekly_reminder.py<br><i>weekly</i>"] --> R["DM inactive<br>users"]
    BF["backfill.py<br><i>weekly</i>"] --> E["Embed, cluster &<br>LLM-summarize"]
```

# Treatment Effect Estimation

Assuming counterfactual stationarity, a Poisson GLM with successive difference contrasts estimates the causal effect of conversion-rate changes as incidence rate ratios. The same model forecasts next week's redemptions.

![Treatment Effect](irr_plot.png){height=45%}

# Topic Clustering

Kudos messages are embedded into 128-dim vectors using a truncated embedding model, then clustered with KMeans using inverse-log month-frequency weights so older high-volume months don't dominate.

$$w_i = \frac{1}{\ln(1 + c_{m_i})} \qquad k = n_{\text{months}} + 3$$
Representative messages (nearest 25% to centroid) are sampled and summarized by an LLM into topic labels. 



# Technology Stack

\begin{center}
\begin{tabular}{c@{\hspace{1.2em}}c@{\hspace{1.2em}}c@{\hspace{1.2em}}c@{\hspace{1.2em}}c}
\includegraphics[height=1.2cm]{logos/slack.png} &
\includegraphics[height=1.2cm]{logos/postgres.png} &
\includegraphics[height=1.2cm]{logos/python.png} &
\includegraphics[height=1.2cm]{logos/dash.png} &
\includegraphics[width=3cm]{logos/statsmodels.png} \\[4pt]
\small Slack & \small Postgres & \small Python & \small Dash & \small statsmodels \\[14pt]
\includegraphics[height=1.2cm]{logos/sklearn.png} &
\includegraphics[height=1.2cm]{logos/llamacpp.png} &
\includegraphics[height=1.2cm]{logos/gemma.png} &
\includegraphics[height=1.2cm]{logos/pgsd.png} & \\[4pt]
\small scikit-learn & \small llama.cpp & \small Gemma & \small pg-schema-diff &
\end{tabular}
\end{center}

# Lines of Code

722 lines total — bot, dashboard, cron jobs, schema, and all business logic.

| Component | Lines |
|-----------|------:|
| Python    |   514 |
| SQL       |   208 |
| **Total** | **722** |

# AI in Development

AI was used at every stage: critiquing the initial design, generating synthetic data (usernames, kudos messages, topic distributions), prototyping all code, tests and debugging, learning unfamiliar libraries (Dash), and writing this presentation.

# AI in the Product

```mermaid
graph LR
    A["Kudos message"] --> B["gemma-4-E2B-it<br>content gate<br>YES/NO, max_tokens=5"]
    B -- "specific" --> C["Point recorded"]
    B -- "vague" --> D["Rejected with<br>example"]
    E["All kudos messages"] --> F["embeddinggemma-300m<br>truncated to 128-dim"]
    F --> G["KMeans clustering"]
    G --> H["LLM summarization<br>topic labels"]
```

The bot uses an LLM to gate every kudos for substantive content, and another to summarize topic clusters for the dashboard.
