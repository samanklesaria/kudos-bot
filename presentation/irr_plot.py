"""Generate a slideshow-scaled IRR plot as presentation/irr_plot.png."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import psycopg
import statsmodels.formula.api as smf
import statsmodels.api as sm
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
DATABASE_URL = os.environ["DATABASE_URL"]

def load_weekly():
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute("SELECT * FROM weekly_kudos").fetchall()
        return pd.DataFrame(rows) if rows else pd.DataFrame()

def fit_irr(df):
    model_df = pd.DataFrame({
        "y": df["redeemed"].astype(float),
        "t": df["conversion_rate"].astype(float)})
    fit = smf.glm("y ~ C(t, Diff)", data=model_df,
        family=sm.families.Poisson()).fit()
    unique_rates = sorted(model_df["t"].unique())
    betas, ses = fit.params[1:], fit.bse[1:]
    return pd.DataFrame([
        dict(rate=r, irr=np.exp(b), lo=np.exp(b - 1.96 * se), hi=np.exp(b + 1.96 * se))
        for r, b, se in zip(unique_rates[1:], betas, ses)])

def main():
    df = load_weekly()
    if df.empty:
        print("No data in weekly_kudos view — nothing to plot.", file=sys.stderr)
        sys.exit(1)
    weeks_per_month = df.groupby("ym")["ym"].transform("size")
    irr = fit_irr(df[weeks_per_month >= 4])

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(irr["rate"], irr["irr"],
        yerr=[irr["irr"] - irr["lo"], irr["hi"] - irr["irr"]],
        fmt="o-", color="#50B86C", capsize=6, linewidth=2.5,
        markersize=10, capthick=2)
    ax.axhline(1, color="grey", linestyle="--", linewidth=1)
    ax.set_xlabel("Conversion rate ($/pt)", fontsize=16)
    ax.set_ylabel("Incidence Rate Ratio", fontsize=16)
    ax.set_title("Dose–Response: Conversion Rate vs Activity (IRR)", fontsize=18)
    ax.tick_params(labelsize=13)
    fig.tight_layout()

    out = os.path.join(os.path.dirname(__file__), "irr_plot.png")
    fig.savefig(out, dpi=200)
    print(out)

if __name__ == "__main__":
    main()
