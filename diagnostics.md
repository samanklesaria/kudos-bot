# Poisson ITS Model Diagnostics

The dashboard's IRR estimates come from pairwise Poisson rate comparisons
across consecutive conversion-rate periods. This is equivalent to a Poisson
GLM with successive-difference contrasts on the treatment period indicator and
$\log(\text{exposure})$ as an offset. This notebook fits the explicit GLM and
runs standard diagnostics to assess whether the Poisson assumptions hold.

## Setup

```python {.marimo}
import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import statsmodels.api as sm
import patsy
from scipy import stats
from dotenv import load_dotenv
from psycopg_pool import ConnectionPool
import psycopg

load_dotenv()
pool = ConnectionPool(os.environ["DATABASE_URL"])
```

## Load data

Weekly kudos counts with exposure (workday fraction $\times$ number of active
users). Weeks with fewer than 4 observations in their month are dropped to
avoid partial-month edge effects.

```python {.marimo}
def query(sql):
    with pool.connection() as conn:
        conn.row_factory = psycopg.rows.dict_row
        return pd.DataFrame(conn.execute(sql).fetchall())

weekly = query("SELECT * FROM weekly_kudos")
covs = query("SELECT * FROM covariates ORDER BY label, week")
pivoted = covs.pivot(index="week", columns="label", values="value")
weekly["exposure"] = weekly["yw"].map(
    (pivoted["workday_frac"] * pivoted["num_users"]).astype(float))
weeks_per_month = weekly.groupby("ym")["ym"].transform("size")
df = weekly[weeks_per_month >= 4].copy().reset_index(drop=True)
df
```

## Model specification

The data-generating process in `simulate.py` is:

$$
Y_w \sim \operatorname{Poisson}(\mu_w), \qquad
\log \mu_w = \log e_w + \mathbf{x}_w^\top \boldsymbol{\beta}
$$

where $e_w = \text{workday\_frac}_w \times \text{num\_users}_w$ is the
exposure and $\mathbf{x}_w$ encodes the treatment period via successive
difference contrasts (`C(t, Diff)` in patsy). Each coefficient $\beta_j$ for
$j \ge 1$ is the log-IRR for period $j$ relative to period $j-1$.

```python {.marimo}
period = pd.Categorical(df["conversion_rate"].rank(method="dense").astype(int))
X = np.asarray(patsy.dmatrix("C(period, Diff)", {"period": period}))
model = sm.GLM(
    df["redeemed"], X,
    family=sm.families.Poisson(),
    offset=np.log(df["exposure"]))
result = model.fit()
print(result.summary())
```

## IRR table

Each row is the incidence rate ratio for one period vs. the previous, with 90%
confidence intervals via score test inversion (matching the dashboard's
`confint_poisson_2indep(..., method="score")`).

```python {.marimo}
from statsmodels.stats.rates import confint_poisson_2indep

agg = df.groupby("conversion_rate").agg(
    count=("redeemed", "sum"), exposure=("exposure", "sum")).sort_index()
rows = []
for (r1, a), (r2, b) in zip(agg.iloc[:-1].iterrows(), agg.iloc[1:].iterrows()):
    if a["exposure"] == 0 or b["exposure"] == 0:
        continue
    irr = (b["count"] / b["exposure"]) / (a["count"] / a["exposure"])
    lo, hi = confint_poisson_2indep(
        b["count"], b["exposure"], a["count"], a["exposure"],
        compare="ratio", method="score", alpha=0.1)
    rows.append(dict(IRR=irr, lo=lo, hi=hi))
irr_table = pd.DataFrame(rows,
    index=[f"Period {i+1} vs {i}" for i in range(len(rows))])
irr_table
```

## Randomized quantile residuals

Pearson and deviance residuals have discrete, non-normal distributions for
count data. Randomized quantile residuals (Dunn & Smyth 1996) fix this: for
each observation $y_i$ with fitted $\hat\mu_i$,

$$
r_i^Q = \Phi^{-1}(u_i), \qquad
u_i \sim \operatorname{Uniform}\bigl[
  F(y_i - 1;\, \hat\mu_i),\;
  F(y_i;\, \hat\mu_i)
\bigr]
$$

where $F$ is the Poisson CDF. If the model is correct, $r_i^Q \sim N(0,1)$.

```python {.marimo}
rng = np.random.default_rng(0)
mu_hat = result.mu
lo_u = stats.poisson.cdf(df["redeemed"] - 1, mu_hat)
hi_u = stats.poisson.cdf(df["redeemed"], mu_hat)
u = rng.uniform(lo_u, hi_u)
qresid = stats.norm.ppf(u)
```

### Q–Q plot

Points should lie close to the diagonal if the Poisson assumption holds.
Systematic curvature suggests misspecified variance (e.g. overdispersion),
while outlying tails suggest individual anomalous weeks.

```python {.marimo}
fig, ax = plt.subplots(figsize=(5, 5))
sm.qqplot(qresid, line="45", ax=ax)
ax.set_title("Q–Q plot of randomized quantile residuals")
ax.set_xlabel("Theoretical quantiles")
ax.set_ylabel("Sample quantiles")
fig.tight_layout()
fig
```

### Residuals vs. fitted values

A funnel or trend here indicates misspecified mean or variance. For a
well-fitting Poisson model, residuals should scatter uniformly around zero
with constant spread.

```python {.marimo}
fig, ax = plt.subplots(figsize=(7, 4))
ax.scatter(mu_hat, qresid, alpha=0.6, s=20)
ax.axhline(0, color="grey", linestyle="--", linewidth=0.8)
ax.set_xlabel("Fitted $\\hat{\\mu}$")
ax.set_ylabel("Quantile residual")
ax.set_title("Residuals vs. fitted")
fig.tight_layout()
fig
```

### Residuals over time

Serial correlation in residuals violates the independence assumption and
inflates confidence in IRR estimates. Look for runs of same-sign residuals.

```python {.marimo}
fig, ax = plt.subplots(figsize=(8, 3))
ax.bar(range(len(qresid)), qresid, color=np.where(qresid > 0, "#50B86C", "#D9534F"), width=0.8)
ax.axhline(0, color="grey", linewidth=0.8)
ax.set_xlabel("Week index")
ax.set_ylabel("Quantile residual")
ax.set_title("Residuals over time")
fig.tight_layout()
fig
```

## Overdispersion

The Poisson model assumes $\operatorname{Var}(Y) = \mu$. If the dispersion
parameter $\hat\phi = \chi^2_P / (n - p)$ is substantially above 1, a
quasi-Poisson or negative binomial model would be more appropriate, and the
Score-based IRR confidence intervals are too narrow.

```python {.marimo}
pearson_chi2 = result.pearson_chi2
n, p = len(df), X.shape[1]
dispersion = pearson_chi2 / (n - p)
print(f"Pearson χ² = {pearson_chi2:.2f}")
print(f"Dispersion = {dispersion:.3f}  (n={n}, p={p})")
if dispersion > 1.5:
    print("⚠ Substantial overdispersion — consider quasi-Poisson or NB2.")
elif dispersion > 1.1:
    print("Mild overdispersion — Poisson CIs may be slightly optimistic.")
else:
    print("No evidence of overdispersion.")
```

## Autocorrelation

The Durbin–Watson statistic tests for first-order serial correlation in
residuals. Values near 2 indicate no autocorrelation; values below 1.5 or
above 2.5 suggest positive or negative autocorrelation respectively.

```python {.marimo}
from statsmodels.stats.stattools import durbin_watson

dw = durbin_watson(qresid)
print(f"Durbin–Watson = {dw:.3f}")
```

ACF plot of quantile residuals. Significant spikes beyond lag 0 indicate
temporal dependence not captured by the period indicators.

```python {.marimo}
fig, ax = plt.subplots(figsize=(7, 3))
sm.graphics.tsa.plot_acf(qresid, lags=min(15, len(qresid) // 2 - 1), ax=ax,
    title="ACF of quantile residuals")
fig.tight_layout()
fig
```

---

# Cluster Diagnostics

The dashboard's topic streamgraph is driven by KMeans clustering of kudos
message embeddings (`cron/backfill.py`). The number of clusters $k$ is
currently set heuristically as $\lfloor n_{\text{months}} + 3.75 \rfloor$.
This section evaluates whether that choice is reasonable and whether the
resulting clusters are well-separated.

## Load embeddings

```python {.marimo}
from pgvector.psycopg import register_vector

with pool.connection() as conn:
    register_vector(conn)
    conn.row_factory = psycopg.rows.dict_row
    rows = conn.execute(
        "SELECT id, embedding, to_char(created_at, 'YYYY-MM') AS month "
        "FROM kudos WHERE embedding IS NOT NULL").fetchall()
emb_df = pd.DataFrame(rows)
embeddings = np.array(emb_df["embedding"].tolist())
embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
print(f"{len(embeddings)} kudos with embeddings, dim={embeddings.shape[1]}")
```

## Elbow plot (inertia)

KMeans inertia (within-cluster sum of squared distances to centroids) should
decrease with $k$. The "elbow" — where the marginal reduction flattens — suggests
a natural number of clusters. A smooth curve with no clear elbow indicates the
data lacks strong cluster structure.

```python {.marimo}
from sklearn.cluster import KMeans

k_range = range(2, min(20, len(embeddings) // 2))
inertias = [KMeans(n_clusters=k, n_init=5, random_state=0).fit(embeddings).inertia_
    for k in k_range]

fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(list(k_range), inertias, "o-", markersize=5)
ax.set_xlabel("$k$")
ax.set_ylabel("Inertia")
ax.set_title("Elbow plot")
fig.tight_layout()
fig
```

## Silhouette scores

The silhouette coefficient for observation $i$ is:

$$
s_i = \frac{b_i - a_i}{\max(a_i,\, b_i)}
$$

where $a_i$ is the mean intra-cluster distance and $b_i$ is the mean
nearest-cluster distance. Values near 1 indicate well-separated clusters;
values near 0 indicate overlap; negative values indicate misassignment. The
mean silhouette score across all points summarizes overall cluster quality.

```python {.marimo}
from sklearn.metrics import silhouette_score, silhouette_samples

sil_scores = [silhouette_score(embeddings,
    KMeans(n_clusters=k, n_init=5, random_state=0).fit_predict(embeddings))
    for k in k_range]

fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(list(k_range), sil_scores, "o-", markersize=5, color="#50B86C")
ax.set_xlabel("$k$")
ax.set_ylabel("Mean silhouette score")
ax.set_title("Silhouette score vs. $k$")
fig.tight_layout()
fig
```

## Silhouette profile (current $k$)

A per-cluster breakdown at the current $k$ reveals whether specific clusters
are poorly separated. Wide, uniform bars are healthy; thin bars or clusters
with many negative-silhouette members suggest they should be merged.

```python {.marimo}
n_months = emb_df["month"].nunique()
k_current = min(int(n_months + 3.75), len(embeddings) - 1)
labels = KMeans(n_clusters=k_current, n_init=10, random_state=0).fit_predict(embeddings)
sil_vals = silhouette_samples(embeddings, labels)

fig, ax = plt.subplots(figsize=(7, 5))
y_lower = 0
for i in range(k_current):
    cluster_sil = np.sort(sil_vals[labels == i])
    ax.barh(range(y_lower, y_lower + len(cluster_sil)), cluster_sil, height=1.0, edgecolor="none")
    y_lower += len(cluster_sil) + 2
ax.axvline(sil_vals.mean(), color="red", linestyle="--", label=f"mean = {sil_vals.mean():.3f}")
ax.set_xlabel("Silhouette coefficient")
ax.set_ylabel("Kudos (grouped by cluster)")
ax.set_title(f"Silhouette profile at $k={k_current}$")
ax.legend()
fig.tight_layout()
fig
```

## Calinski–Harabasz index

The Calinski–Harabasz index (variance ratio criterion) is the ratio of
between-cluster dispersion to within-cluster dispersion, scaled by degrees of
freedom. Higher is better. Unlike silhouette, it is fast to compute and
doesn't require pairwise distances.

```python {.marimo}
from sklearn.metrics import calinski_harabasz_score

ch_scores = [calinski_harabasz_score(embeddings,
    KMeans(n_clusters=k, n_init=5, random_state=0).fit_predict(embeddings))
    for k in k_range]

fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(list(k_range), ch_scores, "o-", markersize=5, color="#4A90D9")
ax.set_xlabel("$k$")
ax.set_ylabel("Calinski–Harabasz index")
ax.set_title("Calinski–Harabasz vs. $k$")
fig.tight_layout()
fig
```

## Cluster stability (subsample agreement)

Cluster solutions should be robust to perturbation. We fit KMeans on two
random 80% subsamples and measure agreement via the Adjusted Rand Index (ARI).
ARI = 1 means perfect agreement; ARI ≈ 0 means random. Low ARI suggests the
clusters are artifacts of noise rather than genuine structure.

```python {.marimo}
from sklearn.metrics import adjusted_rand_score

rng = np.random.default_rng(42)
n = len(embeddings)
idx_a = rng.choice(n, size=int(0.8 * n), replace=False)
idx_b = rng.choice(n, size=int(0.8 * n), replace=False)
overlap = np.intersect1d(idx_a, idx_b)

labels_a = KMeans(n_clusters=k_current, n_init=10, random_state=0).fit_predict(embeddings[idx_a])
labels_b = KMeans(n_clusters=k_current, n_init=10, random_state=0).fit_predict(embeddings[idx_b])

map_a = dict(zip(idx_a, labels_a))
map_b = dict(zip(idx_b, labels_b))
ari = adjusted_rand_score([map_a[i] for i in overlap], [map_b[i] for i in overlap])
print(f"Adjusted Rand Index on overlap (n={len(overlap)}): {ari:.3f}")
if ari > 0.8:
    print("Clusters are highly stable.")
elif ari > 0.5:
    print("Moderate stability — some clusters may be interchangeable.")
else:
    print("⚠ Low stability — cluster boundaries are not robust.")
```

## Next steps for clustering

If diagnostics reveal problems, consider:

- **No clear elbow / low silhouette** → the embedding space may lack discrete
  topic structure. Try reducing dimensionality with UMAP before clustering, or
  use a density-based method (HDBSCAN) that can leave noise points unclustered.
- **Specific weak clusters** → merge clusters with high inter-cluster overlap
  (negative silhouette members), or raise the `frac >= 0.1` threshold in the
  `topic_stream` view to hide noisy clusters from the dashboard.
- **Low subsample ARI** → increase `n_init` in KMeans, or switch to
  spherical KMeans (cosine distance) since the embeddings are L2-normalized.
- **$k$ too high or low** → override the heuristic in `backfill.py` with the
  $k$ that maximizes silhouette or Calinski–Harabasz.

---

## Next steps for the Poisson model

If diagnostics reveal problems, consider:

- **Overdispersion** → refit with `family=sm.families.NegativeBinomial()` or
  use `scale="X2"` for quasi-Poisson standard errors.
- **Autocorrelation** → add a linear time trend or AR(1) working correlation
  via GEE (`sm.GEE` with `cov_struct=sm.cov_struct.Autoregressive()`).
- **Non-linearity in exposure** → add $\log(\text{num\_users})$ as a covariate
  instead of relying solely on the offset.
- **Structural breaks** → if the Q–Q plot shows heavy tails, check for
  anomalous weeks (holidays, outages) and consider indicator variables.
