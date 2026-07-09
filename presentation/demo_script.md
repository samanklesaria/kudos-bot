# Demo Script

## 1. Bot joins a channel

Add the bot to a public channel. It posts an onboarding message with a usage example.

## 2. Vague kudos (rejected)

Post: `@kudos-bot @jane good job`

Bot replies asking for a specific action, with an example of what good kudos looks like.

## 3. Edit to fix

Edit the vague message to: `@kudos-bot @jane Great job leading the incident retro today!`

Bot deletes the old rejection, re-evaluates the edited message, and confirms the kudos with auto-redemption.

## 4. Multiple recipients (rejected)

Post: `@kudos-bot @jane @bob Great teamwork on the deploy!`

Bot replies: "Please give kudos to one person at a time."

## 5. Private channel rejection

Invite the bot to a private channel. It posts a message explaining why and immediately leaves.

## 6. Dashboard walkthrough

Open the dashboard and show each tab:

- **Snapshot cards**: budget, spent this month, queued
- **Usage chart**: weekly bars with budget line and forecast diamond
- **IRR plot**: effect of conversion rate changes
- **Leaderboard**: who's receiving the most kudos
- **Topics**: click a streamgraph band to drill into actual messages
