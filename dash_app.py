"""Kudos accounting dashboard (Dash app).

Panels:
  1. Operational snapshot (budget, spent, queued)
  2. Weekly acquired vs redeemed with budget line + forecast
  3. Dose-response IRR plot (Negative Binomial GLM on conversion_rate)
  4. Recipient point distribution
  5. Topic cluster streamgraph with drill-down table
"""
import os, json
import numpy as np
import pandas as pd
import psycopg
import plotly.graph_objects as go
import statsmodels.formula.api as smf
import statsmodels.api as sm
from scipy import stats
from dash import Dash, html, dcc, callback, Input, Output, State
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

def query(sql, params=None):
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        register_vector(conn)
        rows = conn.execute(sql, params).fetchall()
        return pd.DataFrame(rows) if rows else pd.DataFrame()

def scalar(sql):
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(sql).fetchone()
        return row[0] if row else None

# ── Data loaders ──────────────────────────────────────────────────────────

def load_snapshot():
    budget = scalar(
        "SELECT point_budget FROM budgets WHERE month_date <= CURRENT_DATE "
        "ORDER BY month_date DESC LIMIT 1")
    spent = scalar(
        "SELECT COUNT(*)::int FROM kudos "
        "WHERE redeemed_at IS NOT NULL AND deleted_at IS NULL "
        "AND redeemed_at >= date_trunc('month', NOW())")
    queued = scalar("SELECT COUNT(*)::int FROM pending_redemptions")
    return budget or 0, spent, queued

def load_weekly():
    return query("SELECT * FROM weekly_kudos")

def load_leaderboard():
    return query("SELECT * FROM leaderboard")

def load_stream():
    return query("SELECT * FROM topic_stream")

def load_cluster_messages(cluster_id, month):
    return query("""
        SELECT ug.display_name AS giver, ur.display_name AS recipient,
               k.message_text AS message, k.created_at::date AS date
        FROM kudos k
        JOIN cluster_members cm ON cm.kudos_id = k.id
        JOIN users ug ON ug.id = k.giver_id
        JOIN users ur ON ur.id = k.recipient_id
        WHERE cm.cluster_id = %(cid)s
          AND to_char(k.created_at, 'YYYY-MM') = %(month)s
          AND k.deleted_at IS NULL
        ORDER BY k.created_at DESC LIMIT 50""",
        {"cid": cluster_id, "month": month})

def load_covariates():
    return query("SELECT * FROM covariates ORDER BY label, week")

def fit_its(df):
    """Fit Poisson GLM: redeemed ~ C(conversion_rate, Diff) [+ covariates]. Return IRR df + forecast."""
    model_df = pd.DataFrame({"y": df["redeemed"].astype(float),
        "t": df["conversion_rate"].astype(float),
        "yw": df["yw"]})
    covs = load_covariates()
    cov_names = []
    exposure = None
    if not covs.empty:
        for label, grp in covs.groupby("label"):
            if grp["value"].nunique() <= 1:
                continue
            cov_map = dict(zip(grp["week"], grp["value"].astype(float)))
            model_df[label] = model_df["yw"].map(cov_map)
            if model_df[label].isna().any():
                model_df = model_df.drop(columns=[label])
            elif label == "workday_frac":
                exposure = model_df.pop(label)
            else:
                if label == "channel_messages":
                    model_df[label] = np.log(model_df[label])
                cov_names.append(label)
    formula = "y ~ C(t, Diff)" + "".join(f" + {c}" for c in cov_names)
    model_df = model_df.drop(columns=["yw"])
    fit = smf.glm(formula, data=model_df,
        family=sm.families.Poisson(),
        exposure=exposure).fit()
    # IRR table: skip intercept and covariates, exponentiate difference coefficients only
    unique_rates = sorted(model_df["t"].unique())
    n_diff = len(unique_rates) - 1
    betas, ses = fit.params[1:1 + n_diff], fit.bse[1:1 + n_diff]
    irr_rows = [dict(rate=r, irr=np.exp(b), lo=np.exp(b - 1.96 * se), hi=np.exp(b + 1.96 * se))
        for r, b, se in zip(unique_rates[1:], betas, ses)]
    irr_df = pd.DataFrame(irr_rows)
    # Forecast next week at latest conversion rate (prediction interval)
    pred_data = pd.DataFrame({"t": [unique_rates[-1]]})
    for c in cov_names:
        pred_data[c] = [model_df[c].iloc[-1]]
    pred = fit.get_prediction(pred_data)
    mu = pred.predicted[0]
    lo = stats.poisson.ppf(0.025, mu)
    hi = stats.poisson.ppf(0.975, mu)
    return irr_df, dict(median=mu, lo=lo, hi=hi)

# ── Layout ────────────────────────────────────────────────────────────────

load_figure_template("flatly")

app = Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])
app.layout = dbc.Container([
    dbc.Row(dbc.Col(html.H1("Kudos Dashboard", className="my-3"))),
    dcc.Tabs([
        dcc.Tab(label="Usage & Budget", children=[
            dbc.Row(id="snapshot", className="g-3 my-3"),
            dcc.Graph(id="usage-plot", style={"height": "450px"}),
            dcc.Graph(id="irr-plot", style={"height": "400px"})]),
        dcc.Tab(label="Leaderboard", children=[
            dcc.Graph(id="leaderboard-plot")]),
        dcc.Tab(label="Topics", children=[
            dcc.Graph(id="stream-plot"),
            html.Div(id="topic-label", className="my-2 fw-bold"),
            dag.AgGrid(id="topic-table", defaultColDef={"resizable": True, "sortable": True,
                "wrapText": True, "autoHeight": True, "cellStyle": {"lineHeight": "1.4"}},
                dashGridOptions={"pagination": True, "paginationPageSize": 20,
                    "rowStyle": {"paddingTop": "2px", "paddingBottom": "2px"}})])]),
    dcc.Store(id="stream-data"),
    dcc.Store(id="enriched-click")], fluid=True)

# ── Callbacks ─────────────────────────────────────────────────────────────

# Convert fill-click xPixel to month using Plotly's axis
app.clientside_callback(
    """function(clickData) {
        if (!clickData || !clickData.points || !clickData.points.length) return null;
        var pt = clickData.points[0];
        if (pt.x) return clickData;
        var el = document.querySelector('#stream-plot .js-plotly-plot');
        if (!el || !el._fullLayout) return clickData;
        var xa = el._fullLayout.xaxis;
        var xData = xa.p2d(pt.xPixel - xa._offset);
        var dateStr = new Date(xData).toISOString().slice(0, 7);
        return {points: [{curveNumber: pt.curveNumber, x: dateStr}]};
    }""",
    Output("enriched-click", "data"),
    Input("stream-plot", "clickData"))

@callback(
    Output("snapshot", "children"),
    Output("usage-plot", "figure"),
    Output("irr-plot", "figure"),
    Input("snapshot", "id"))
def update_usage(_):
    budget, spent, queued = load_snapshot()
    snap = [dbc.Col(dbc.Card(dbc.CardBody([
        html.H6(label, className="card-subtitle text-muted"),
        html.H3(f"{val} pts")]), className="shadow-sm"), md=4)
        for label, val in [("Monthly Budget", budget), ("Spent This Month", spent), ("Queued", queued)]]

    df = load_weekly()
    if df.empty:
        empty = go.Figure()
        return snap, empty, empty

    df["x"] = range(len(df))
    weekly_budget = df["point_budget"].astype(float) / 4

    # Usage chart
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["x"], y=df["acquired"], name="Acquired",
        marker_color="#50B86C"))
    fig.add_trace(go.Bar(x=df["x"], y=df["redeemed"], name="Redeemed",
        marker_color="#4A90D9"))
    fig.add_trace(go.Scatter(x=df["x"] - 0.5, y=weekly_budget, name="Budget/4",
        mode="lines", line=dict(dash="dot", color="red")))

    # Month labels on x-axis
    month_ticks = df.groupby("ym")["x"].first()
    fig.update_layout(
        barmode="group", title="Weekly Kudos Acquired & Redeemed",
        xaxis=dict(tickvals=month_ticks.values, ticktext=[
            pd.Timestamp(m + "-01").strftime("%b %Y") for m in month_ticks.index]),
        yaxis_title="Points", legend=dict(orientation="h", y=1.12))

    # Forecast
    try:
        weeks_per_month = df.groupby("ym")["ym"].transform("size")
        full_df = df[weeks_per_month >= 4]
        irr_df, fc = fit_its(full_df)
        fx = df["x"].max() + 2
        fig.add_trace(go.Scatter(x=[fx], y=[fc["median"]], mode="markers",
            marker=dict(symbol="diamond", size=10, color="#4A90D9"),
            name="Forecast", error_y=dict(type="data", symmetric=False,
                array=[fc["hi"] - fc["median"]], arrayminus=[fc["median"] - fc["lo"]])))
    except Exception:
        irr_df = pd.DataFrame()

    # IRR plot
    irr_fig = go.Figure()
    if not irr_df.empty:
        irr_fig.add_trace(go.Scatter(
            x=irr_df["rate"], y=irr_df["irr"], mode="markers+lines",
            marker=dict(size=8, color="#50B86C"),
            error_y=dict(type="data", symmetric=False,
                array=(irr_df["hi"] - irr_df["irr"]).tolist(),
                arrayminus=(irr_df["irr"] - irr_df["lo"]).tolist())))
        irr_fig.update_layout(title="Dose-Response: Conversion Rate vs Activity (IRR)",
            xaxis_title="Conversion rate ($/pt)", yaxis_title="Incidence Rate Ratio")

    return snap, fig, irr_fig

@callback(Output("leaderboard-plot", "figure"), Input("leaderboard-plot", "id"))
def update_leaderboard(_):
    df = load_leaderboard()
    if df.empty:
        return go.Figure()
    fig = go.Figure(go.Bar(
        y=df["display_name"][::-1], x=df["received"][::-1],
        orientation="h", marker_color="#4A90D9"))
    fig.update_layout(title="Points Received per Person", xaxis_title="Points",
        height=max(400, len(df) * 22))
    return fig

@callback(
    Output("stream-plot", "figure"),
    Output("stream-data", "data"),
    Input("stream-plot", "id"))
def update_stream(_):
    df = load_stream()
    if df.empty:
        return go.Figure().update_layout(
            title="Topic Evolution (no cluster data yet)"), None
    fig = go.Figure()
    import plotly.express as px
    summaries = df["summary"].unique()
    colors = px.colors.qualitative.Dark24 + px.colors.qualitative.Light24
    for i, summary in enumerate(summaries):
        sub = df[df["summary"] == summary]
        fig.add_trace(go.Scatter(
            x=sub["month"], y=sub["frac"], name=summary,
            stackgroup="one", mode="lines", hoveron="points+fills",
            line=dict(color=colors[i % len(colors)])))
    fig.update_layout(title="Topic Evolution Over Time", yaxis_title="Fraction of kudos",
        yaxis_tickformat=".0%")
    store = {"records": df.to_dict("records"),
        "summaries": list(df["summary"].unique())}
    return fig, json.dumps(store)

@callback(
    Output("topic-label", "children"),
    Output("topic-table", "columnDefs"),
    Output("topic-table", "rowData"),
    Input("enriched-click", "data"),
    State("stream-data", "data"))
def drill_topic(click, store):
    cols = [{"field": "giver", "flex": 1}, {"field": "recipient", "flex": 1},
        {"field": "message", "flex": 11}]
    if not click or not store:
        return "Click a band to see messages.", cols, []
    pt = click["points"][0]
    data = json.loads(store)
    records, summaries = data["records"], data["summaries"]
    curve = pt.get("curveNumber", 0)
    summary = summaries[curve] if curve < len(summaries) else ""
    month = pt.get("x", "")[:7]
    if not month:
        return f"Topic: {summary}", cols, []
    hits = [r for r in records if r["summary"] == summary and r["month"] == month]
    if not hits:
        # Snap to nearest available month for this topic
        topic_months = sorted(set(r["month"] for r in records if r["summary"] == summary))
        if topic_months:
            month = min(topic_months, key=lambda m: abs(int(m.replace("-", "")) - int(month.replace("-", ""))))
            hits = [r for r in records if r["summary"] == summary and r["month"] == month]
    if not hits:
        return f"Topic: {summary} ({month})", cols, []
    cluster_id = hits[0]["cluster_id"]
    msgs = load_cluster_messages(cluster_id, month)
    return f"Topic: {summary} ({month})", cols, msgs.to_dict("records")

if __name__ == "__main__":
    app.run(debug=not os.environ.get("DASH_PROD"))
