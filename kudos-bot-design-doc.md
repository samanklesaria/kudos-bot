# Kudos Bot — Design Document

## 1. Purpose

A Slack-based peer recognition system that lets employees publicly appreciate each other's work. Recognition ("kudos") accumulates as points that get redeemed for money, with the conversion rate and monthly budget controlled by accounting/management.

The design goals, in priority order:
1. Keep the giving mechanism simple and frictionless (no tiers, no point multipliers).
2. Make abuse (reciprocal farming, self-dealing) structurally difficult rather than relying on policy alone.
3. Give accounting full control over monthly financial exposure and visibility into backlog.

## 2. Core Concepts

**Kudos**: A public message in an approved channel, tagging the kudos-bot and a recipient, that awards the recipient one point.

**Appreciator**: The person giving kudos.

**Recipient**: The person receiving kudos.

**Budget**: A monthly point-expenditure cap set by accounting. Once exhausted, further claims that month are queued.

**Conversion Rate**: A monthly points-to-dollars exchange rate set by accounting.

## 3. System Flow

### 3.1 Giving Kudos
- Kudos can be given in any public channel where the `@kudos-bot` is a member. If the bot is added to a private channel, it will immediately leave: praise is meant to be public!
- A user gives kudos by tagging `@kudos-bot` and the recipient in a public channel, along with a message about what they did. These messages are checked for substantive content by an LLM. Kudos with false claims are not eligeable for monetary reimbursement. 
- Each appreciator may give at most **one kudos per day**. This is a cap on the *giver*, not the recipient — a single person can still be recognized by many different appreciators in the same week.
- Each appreciator can recognize a given recipient at most once per month. Self-kudos is blocked.
- The bot sends a gentle reminder to users who haven't given kudos in a week to encourage consistent participation.

### 3.2 Redeeming Kudos
- By itself, being given kudos doesn't get you anything besides esteem from your collegues.
- To turn your accumulated kudos into points, you have to give kudos yourself! Every time the number of kudos you've given matches the number of kudos you've recieved, you get a monetary reward. 
- The dollar-per-point conversion rate is set on a month-by-month basis by management. 

### 3.3 Monthly Budget and Queueing
- Accounting sets a point-expenditure budget per calendar month.
- Claims are applied against the current month's budget as they come in.
- If the budget is exhausted, new claims are **queued** rather than rejected. If this occurs, the bot informs the user whose transactions got queued exactly once per month that that have a pending kudos payout. 
- Queued claims are processed **FIFO** and consume the **following month's** budget first, before any new claims made during that month are processed.
- The first time a rollover occurs in a given month, the bot proactively notifies accounting, so a developing backlog isn't discovered silently.
- A dashboard exists for accounting showing current queue depth/backlog over time, so chronic underbudgeting is visible at the budget-setting level.

### 3.4 Deletion / Undo
- If a kudos message is deleted, its associated point is removed (this is the designated "undo" path — there is no separate retraction command).
- If the point has already been claimed and reimbursed before the deletion occurs, removing it will push the recipient's balance **negative**. This is accepted as the simplest mechanical behavior, but it does not by itself recover money already paid out.
- Accounting is notified whenever a deletion causes a negative balance, since this represents an already-reimbursed point being retracted — a case worth human review (mistake vs. potential abuse), separate from the routine rollover notification.

## 6. Accounting Dashboard

A dashboard for accounting, referenced in §3.3, that goes beyond a single backlog metric to give a fuller operational and trend picture.

**Operational snapshot (current month)**
- Current month's budget
- Amount spent so far this month
- Amount currently queued/deferred, in both points and number of distinct people waiting

**Treatment Effect Estimation**
- For each conversion rate change, estimates of treatment effect on activity rates that this change induced compared to the previous conversion rate is estimated with a simple negative binomial GLM. Show as a dose response plot with error bars. 

**Kudos Sent and Spent over Time**
- Horizontal budget lines make it clear when monthly spending has gone over budget. 
- A forecast of spending for the next month is also provided along with error bars. These use the same interrupted timeseries model as the treatment effect estimates. 

**Giving/receiving activity over time**
- Total kudos given per week or month, to track participation trends (e.g. growth, plateau, or decline) and provide context for interpreting the budget forecast — a forecast built during a period of declining participation means something different than one built on steady-state activity

**Distribution of points across recipients**
- A histogram or similar view showing whether recognition is broadly spread (most recipients with a small number of points) or concentrated in a few individuals — a quick visual sanity check related to the fairness/abuse considerations in §5, independent of any deeper anomaly-detection work

**Kudos message themes**
- Clustering of kudos message content into themes (e.g. collaboration, mentorship, technical excellence, going above-and-beyond), with the mix of themes tracked over time rather than shown only as a single static snapshot
- Clicking on a theme in a specific month shows a table of the actual messages and participants.
