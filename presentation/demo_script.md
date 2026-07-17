## Bot Walkthrough

Clear the database: `psql $DATABASE_URL <scripts/setup.sql`

Open https://app.slack.com/client/E0BEK3Q98GZ in both Chrome and Firefox

In firefox, open https://app.slack.com/manage/E0BEK3Q98GZ/channels and make a channel.

Add Lisa, Bob and Carol.

Invite @kudos to the channel. 

Have Carol give good kudos to Lisa.

Have Carol give good kudos to Bob and fail. 

Clear the database. 

Have Carol give bad kudos to Bob and fail

Edit the message so that the kudos is good. 

Have Lisa give Carol good kudos.

Clear the database

Try to give kudos to multiple people.

Invite the bot to a private channel. It posts a message explaining why and immediately leaves.

Run the accounting script. 

Clear the database

Run the weekly reminder script

## Dashboard walkthrough

Open the dashboard and show each tab:

- **Snapshot cards**: budget, spent this month, queued
- **Usage chart**: weekly bars with budget line and forecast diamond
- **IRR plot**: effect of conversion rate changes
- **Leaderboard**: who's receiving the most kudos
- **Topics**: click a streamgraph band to drill into actual messages
