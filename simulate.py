"""Simulate kudos data with interrupted timeseries counts and evolving topic clusters.

1. Define monthly budgets with increasing conversion rates (interventions for ITS).
2. Simulate weekly event counts from Poisson distributions with rates adjusted at each budget change.
3. Assign each event a user pair and a topic drawn from time-varying mixture weights.
   Topics: 4 base topics in month 1, +1 new topic each subsequent month.
4. Write kudos, budgets, users to the database.
"""
import os

import csv
import patsy
import numpy as np
import psycopg
from dotenv import load_dotenv
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from sizecheck import sizecheck

from cron.backfill import main as backfill

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
USERNAMES_FILE = os.path.join(os.path.dirname(__file__), "usernames.txt")
N_MONTHS = 8
BASE_TOPICS = 3

def load_messages():
    with open(os.path.join(os.path.dirname(__file__), "kudos_specific_messages.csv")) as f:
        rows = list(csv.DictReader(f))
    texts = np.array([r["text"] for r in rows])
    targets = np.array([int(r["topic_ix"]) for r in rows])
    return texts, targets

def make_budgets(now, monthly_mu):
    return [
        {"month_date": now - relativedelta(months=N_MONTHS - 1 - month),
        "point_budget": int(monthly_mu[month]) // 10 * 10,
        "conversion_rate": max(1, month * 10)}
        for month in range(N_MONTHS)]

@sizecheck
def build_topic_weights(rng):
    max_topics = BASE_TOPICS + N_MONTHS - 1
    months_M1 = np.arange(N_MONTHS)[:, None]
    topics_1T = np.arange(max_topics)[None, :]
    active_MT = topics_1T < BASE_TOPICS + months_M1
    # Base topics decay; new topics enter at comparable weight
    decay_MT = np.where(topics_1T < BASE_TOPICS, 0.8 ** months_M1, 0.0)
    new_MT = np.where((topics_1T >= BASE_TOPICS) & active_MT, 0.25, 0.0)
    w_MT = decay_MT + new_MT
    noise_MT = rng.normal(0, 0.02, (N_MONTHS, max_topics)) * active_MT
    w_MT += np.cumsum(noise_MT, axis=0)
    w_MT *= active_MT
    w_MT /= w_MT.sum(axis=1, keepdims=True)
    return w_MT

def simulate_covariates(rng):
    """Simulate weekly covariates: num_users, workday_frac."""
    W = N_MONTHS * 4
    num_users = 20 + np.cumsum(rng.integers(2, 6, size=N_MONTHS))
    num_users_W = np.repeat(num_users, 4) + rng.integers(-2, 3, size=W)
    # Most weeks are full; ~15% have a holiday
    workday_frac_W = np.where(rng.random(W) < 0.85, 1.0, rng.choice([0.6, 0.8], size=W))
    return {"num_users": num_users_W, "workday_frac": workday_frac_W}

def simulate_ITS_counts(rng, covariates):
    "Simulate weekly counts from a Poisson ITS with covariates, capped around 20/week"
    months = np.arange(N_MONTHS)
    beta = np.full(N_MONTHS, -1.0)
    beta[1:] = 0.1 / months[1:] ** 2
    data = {'t': np.repeat(months, 4)}
    X = np.asarray(patsy.dmatrix("C(t, Diff)", data))
    mu = covariates["workday_frac"] * covariates["num_users"] * np.exp(X @ beta)
    mu_M4 = mu.reshape((N_MONTHS, 4))
    return rng.poisson(mu).reshape((N_MONTHS, 4)), mu_M4

def clear_db(conn):
    conn.execute("DELETE FROM cluster_members")
    conn.execute("DELETE FROM clusters")
    conn.execute("DELETE FROM kudos")
    conn.execute("DELETE FROM budgets")
    conn.execute("DELETE FROM covariates")
    conn.execute("DELETE FROM users")

def write_db(users, budgets, records, rng, covariates, now):
    with psycopg.connect(DATABASE_URL) as conn:
        clear_db(conn)
        for display_name in users:
            conn.execute(
                "INSERT INTO users (id, display_name) VALUES (%s, %s)", (display_name, display_name))
        for cfg in budgets:
            conn.execute(
                "INSERT INTO budgets (month_date, point_budget, conversion_rate) VALUES (%s, %s, %s)",
                (cfg["month_date"], cfg["point_budget"], cfg["conversion_rate"]))
        for label, values in covariates.items():
            for w, val in enumerate(values):
                week_date = week_to_date(now, w)
                yw = week_date.isocalendar()
                week_str = f"{yw[0]:04d}-{yw[1]:02d}"
                conn.execute(
                    "INSERT INTO covariates (label, week, value) VALUES (%s, %s, %s)",
                    (label, week_str, float(val)))
        for i, (giver, recipient, text, kudos_at) in enumerate(records):
            should_redeem = rng.random() < 0.7
            redeemed_at = kudos_at + timedelta(hours=float(rng.exponential(48))) if should_redeem else None
            conn.execute(
                "INSERT INTO kudos (giver_id, recipient_id, channel_id, message_ts, message_text, "
                "created_at, redeemed_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (giver, recipient, "sim", f"{i}", text, kudos_at, redeemed_at))

@sizecheck
def choose_texts(counts_M4, texts_N, topics_N, weights_MT, rng):
    c_WT = rng.multinomial(counts_M4, weights_MT[:,None,:]).reshape(-1, T)
    order_N = np.argsort(topics_N, kind="stable") # text indices sorted by topic
    pool_T = np.bincount(topics_N, minlength=T) # how many texts per topic
    offsets_T = np.concatenate([[0], np.cumsum(pool_T)[:-1]]) # where each topic's block starts in `order`
    flat_c  = c_WT.ravel()
    topics_S = np.repeat(np.tile(np.arange(T), W), flat_c)
    week_S = np.repeat(np.repeat(np.arange(W), T), flat_c)
    nt_S = pool_T[topics_S]
    pos_S      = rng.integers(0, nt_S)
    ix_S = order_N[offsets_T[topics_S] + pos_S]
    return texts_N[ix_S], week_S

def week_to_date(now, week):
    return now - relativedelta(months=N_MONTHS - 1 - int(week) // 4) + \
        relativedelta(weeks=int(week) % 4)

def events(now, users, texts_S, weeks_S, rng):
    weights = rng.pareto(a=1.5, size=len(users))
    weights /= weights.sum()
    for (text, week) in zip(texts_S, weeks_S):
        giver, recipient = rng.choice(users, size=2, replace=False, p=weights)
        yield (giver, recipient, text, week_to_date(now, week))

def main(seed=42):
    rng = np.random.default_rng(seed)
    texts, targets = load_messages()
    now = date.today().replace(day=1)
    covariates = simulate_covariates(rng)
    weights_MT = build_topic_weights(rng)
    counts_M4, mu_M4 = simulate_ITS_counts(rng, covariates)
    budgets = make_budgets(now, mu_M4.sum(axis=1))
    texts_S, weeks_S = choose_texts(counts_M4, texts, targets, weights_MT, rng)
    with open(USERNAMES_FILE) as f:
        users = [line.strip() for line in f if line.strip()]
    records = list(events(now, users, texts_S, weeks_S, rng))
    write_db(users, budgets, records, rng, covariates, now)
    backfill()

if __name__ == "__main__":
    main()
