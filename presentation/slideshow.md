# Kudos Bot

Peer recognition programs fail when they're hard to use or easy to game. *Kudos Bot* makes giving praise as simple as a Slack message — and makes abuse structurally impossible rather than policy-dependent.

# The Problem

```mermaid
graph LR
    A["Forms, portals,<br>manager approval"] -->|"Slack Bot"| B["One Slack message"]
    C["Reciprocal<br>farming"] -->|"Reciprocity +<br>LLM gate"| D["Must give genuine<br>praise to earn"]
    E["Unknown effect of<br>budget on activity"] -->|"ITS dashboard"| F["Causal estimation<br>of spend impact"]
```

# Design Principles

```mermaid
graph TD
    A["<b>Simple for the giver</b><br>@kudos @jane Great retro!"] --> B["<b>Hard to game</b><br>Rate limits, public channels,<br>LLM content gate"]
    A --> C["<b>Budget control</b><br>Monthly cap"]
    A --> D["<b>Measurable</b><br>Causal effect<br>estimation"]
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
    L1["No self-kudos"] --> L2["Rate limits<br><i>1/day, 1/recipient/month</i>"]
    L2 --> L3["Public channels only<br><i>no shadow networks</i>"]
    L3 --> L4["LLM content gate<br><i>rejects vague praise</i>"]
```

- Rate limits and public channels prevent reciprocal farming
- LLM gate produces a written record that praise was substantive — if a claim is false, the org won't be liable

# Budget Control

Monthly point budget and conversion rate set by accounting. Over-budget kudos are marked as overflow — payout opportunity lost.

|                | Time  | Given | Received | Budget           | Redemption |
|----------------|------:|------:|---------:|-----------------:|-----------:|
| Alice Gave     | 10:00 |     2 |        2 |                3 |          1 |
| Bob Received   | 10:00 |     3 |        3 |                2 |          1 |
| Alice Gave     | 11:00 |     3 |        3 |                1 |          1 |
| Bob Received   | 11:00 |     4 |        4 |                0 |          0 |

Bob's second receipt arrived after the budget was exhausted — it overflows and he earns nothing despite otherwise being owed.

# Demo: Slack Bot

Live demo: onboarding, content check, edit-to-fix, accounting cron job, weekly reminder, and private channel rejection.

# Demo: Dashboard

Live demo: operational snapshot, usage & budget forecast, treatment effect plot, leaderboard, and topic drill-down.

# Architecture

All business logic lives in Postgres functions. The Python app is a 136-line event router. 33 automated tests cover all business logic.

```mermaid
graph LR
    S["Slack event"] --> A["app.py<br>136 lines"]
    A --> GK["give_kudos()"]
    GK --> CL["check_kudos_limits()"]
    GK --> TR["try_redeem()<br>redeem or overflow"]
    TR --> A
    A --> Reply["Post reply"]
```

Edits hard-delete the old kudos (un-redeeming linked points) and re-evaluate from scratch.

# Scheduled Jobs

| Script | Frequency | Purpose |
|--------|-----------|---------|
| `accounting.py` | Monthly | Report last month's redemptions to accounting channel |
| `weekly_reminder.py` | Weekly | DM users who haven't given kudos |
| `backfill.py` | Weekly | Embed kudos, cluster, LLM-summarize topics |
| `record_users.py` | Weekly | Record exposures for Poisson GLM |

# Treatment Effect Estimation

- Pairwise IRR between consecutive conversion-rate periods
- Redeemed counts $Y_j$, exposures $E_j = \sum (\text{workday\_frac} \times \text{num\_users})$

$$\text{IRR} = \frac{Y_2 / E_2}{Y_1 / E_1} \qquad \text{CI via score test inversion (Gu et al.)}$$

- 90% confidence intervals on each IRR
- Forecast: Poisson prediction scaled by next week's exposure

# Topic Clustering

Kudos messages are embedded into 128-dim vectors using a truncated embedding model, then clustered with KMeans using inverse-log month-frequency weights so older high-volume months don't dominate.

$$w_i = \frac{1}{\ln(1 + c_{m_i})} \qquad k = n_{\text{months}} + 3$$
Representative messages (nearest 25% to centroid) are sampled and summarized by an LLM into topic labels. Managers can see what behaviors are being recognized and how themes shift over time.

# Demo: Diagnostics Notebook

Live demo: Poisson model diagnostics (quantile residuals, overdispersion, autocorrelation) and cluster diagnostics (elbow plot, silhouette scores, stability).

# Technology Stack

\begin{center}
\begin{tabular}{c@{\hspace{1.2em}}c@{\hspace{1.2em}}c@{\hspace{1.2em}}c@{\hspace{1.2em}}c}
\includegraphics[height=1.2cm]{logos/slack.png} &
\includegraphics[height=1.2cm]{logos/postgres.png} &
\includegraphics[height=1.2cm]{logos/python.png} &
\includegraphics[height=1.2cm]{logos/dash.png} &
\includegraphics[height=1.2cm]{logos/statsmodels.png} \\[4pt]
\small Slack & \small Postgres & \small Python & \small Dash & \small statsmodels \\[14pt]
\includegraphics[height=1.2cm]{logos/sklearn.png} &
\includegraphics[height=1.2cm]{logos/llamacpp.png} &
\includegraphics[height=1.2cm]{logos/gemma.png} &
\includegraphics[height=1.2cm]{logos/pgsd.png} & \\[4pt]
\small scikit-learn & \small llama.cpp & \small Gemma & \small pg-schema-diff &
\end{tabular}
\end{center}

# Lines of Code

842 lines total — bot, dashboard, cron jobs, schema, and all business logic.

| Component | Lines |
|-----------|------:|
| Python    |   634 |
| SQL       |   208 |
| **Total** | **842** |

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

# Questions?

- One Slack message to give kudos — no forms, no approvals
- Anti-abuse enforced structurally, not by policy
- Causal measurement of budget impact on activity
- 842 lines of code, 33 tests, fully automated
