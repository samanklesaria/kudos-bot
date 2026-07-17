"""Kudos accounting dashboard (Dash app).

Panels:
  1. Operational snapshot (budget, spent, queued)
  2. Weekly acquired vs redeemed with budget line + forecast
  3. Dose-response IRR plot (Poisson GLM on conversion_rate)
  4. Recipient point distribution
  5. Topic cluster streamgraph with drill-down table
"""
import os, json
import pandas as pd
import psycopg
from psycopg_pool import ConnectionPool
import plotly.graph_objects as go
import plotly.express as px
from scipy import stats
from dash import Dash, html, dcc, callback, Input, Output, State
import dash_ag_grid as dag
import dash_bootstrap_components as dbc
from dash_bootstrap_templates import load_figure_template
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
pool = ConnectionPool(DATABASE_URL, configure=register_vector)

def query(sql, params=None):
    with pool.connection() as conn:
        conn.row_factory = psycopg.rows.dict_row
        rows = conn.execute(sql, params).fetchall()
        return pd.DataFrame(rows) if rows else pd.DataFrame()

def scalar(sql):
    with pool.connection() as conn:
        row = conn.execute(sql).fetchone()
        return row[0] if row else None

# ── Data loaders ──────────────────────────────────────────────────────────

def load_snapshot():
    budget = scalar(
        "SELECT point_budget FROM effective_budget()")
    spent = scalar(
        "SELECT COUNT(*)::int FROM kudos "
        "WHERE redeemed_at IS NOT NULL AND deleted_at IS NULL "
        "AND redeemed_at >= date_trunc('month', NOW())")
    overflow = scalar(
        "SELECT COUNT(*)::int FROM kudos "
        "WHERE (giver_overflow OR recipient_overflow) AND deleted_at IS NULL "
        "AND created_at >= date_trunc('month', NOW())")
    return budget or 0, spent, overflow

def load_weekly():
    return query("SELECT * FROM weekly_kudos")

def load_leaderboard():
    return query("SELECT * FROM leaderboard")

def load_stream():
    return query("SELECT * FROM topic_stream")

def load_cluster_messages(cluster_id, month):
    return query("""
        SELECT km.giver, km.recipient, km.message, km.date
        FROM kudos_messages km
        JOIN cluster_members cm ON cm.kudos_id = km.id
        WHERE cm.cluster_id = %(cid)s AND km.month = %(month)s
        ORDER BY km.date DESC LIMIT 50""",
        {"cid": cluster_id, "month": month})

def load_user_messages(display_name):
    return query("""
        SELECT giver, recipient, message, date
        FROM kudos_messages
        WHERE recipient = %(name)s
        ORDER BY date DESC LIMIT 50""",
        {"name": display_name})

def load_covariates():
    return query("SELECT * FROM covariates ORDER BY label, week")

def _exposure_by_week(df):
    """Compute per-week exposure (workday_frac * num_users) from covariates, keyed by yw."""
    covs = load_covariates()
    pivoted = covs.pivot(index="week", columns="label", values="value")
    exposure = (pivoted["workday_frac"] * pivoted["num_users"]).astype(float)
    return df["yw"].map(exposure)

def fit_its(df):
    """Pairwise IRR + CI for consecutive conversion rates via Poisson 2-sample comparison."""
    from statsmodels.stats.rates import confint_poisson_2indep
    df = df.copy()
    df["exposure"] = _exposure_by_week(df)
    rate_month = df.groupby("conversion_rate")["ym"].first()
    agg = df.groupby("conversion_rate").agg(
        count=("redeemed", "sum"), exposure=("exposure", "sum")).sort_index()
    if len(agg) < 2:
        return pd.DataFrame(), None
    rows = []
    for (r1, a), (r2, b) in zip(agg.iloc[:-1].iterrows(), agg.iloc[1:].iterrows()):
        lo, hi = confint_poisson_2indep(b["count"], b["exposure"], a["count"], a["exposure"],
            compare="ratio", method="score", alpha=0.1)
        irr = (b["count"] / b["exposure"]) / (a["count"] / a["exposure"])
        rows.append(dict(month=rate_month[r2], rate=r2, irr=irr, lo=lo, hi=hi))
    irr_df = pd.DataFrame(rows)
    # Forecast next week — scale rate by last week's exposure to get counts
    last = agg.iloc[-1]
    last_exposure = df["exposure"].iloc[-1]
    mu = last["count"] / last["exposure"] * last_exposure
    lo = stats.poisson.ppf(0.025, mu)
    hi = stats.poisson.ppf(0.975, mu)
    return irr_df, dict(median=mu, lo=lo, hi=hi)

# ── Layout ────────────────────────────────────────────────────────────────

load_figure_template("flatly")

app = Dash(__name__, external_stylesheets=[dbc.themes.FLATLY])
server = app.server
_scroll = {"overflowY": "auto", "height": "calc(100vh - 120px)"}
_grid_opts = {"pagination": True, "paginationPageSize": 20,
    "rowStyle": {"paddingTop": "2px", "paddingBottom": "2px"}}
_col_defs = {"resizable": True, "sortable": True,
    "wrapText": True, "autoHeight": True, "cellStyle": {"lineHeight": "1.4"}}

app.layout = dbc.Container([
    html.H1("Kudos Dashboard", className="my-2"),
    dcc.Tabs([
        dcc.Tab(label="Usage & Budget", children=[
            html.Div(style=_scroll, children=[
                dbc.Row(id="snapshot", className="g-2 my-2"),
                dcc.Graph(id="usage-plot", style={"height": "38vh"}),
                dcc.Graph(id="irr-plot", style={"height": "30vh"})])]),
        dcc.Tab(label="Leaderboard", children=[
            html.Div(dcc.Graph(id="leaderboard-plot"),
                style={"overflowY": "auto", "height": "calc(50vh - 60px)"}),
            html.Div(id="leaderboard-label", className="my-1 fw-bold"),
            html.Div(dag.AgGrid(id="leaderboard-table", defaultColDef=_col_defs,
                dashGridOptions=_grid_opts),
                style={"overflowY": "auto", "height": "calc(50vh - 60px)"})]),
        dcc.Tab(label="Topics", children=[
            html.Div(style=_scroll, children=[
                dcc.Graph(id="stream-plot", style={"height": "40vh"}),
                html.Div(id="topic-label", className="my-1 fw-bold"),
                dag.AgGrid(id="topic-table", defaultColDef=_col_defs,
                    dashGridOptions=_grid_opts)])])]),
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

def _build_usage_chart(df):
    df["x"] = range(len(df))
    weekly_budget = df["point_budget"].astype(float) / 4
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df["x"], y=df["acquired"], name="Acquired",
        marker_color="#50B86C"))
    fig.add_trace(go.Bar(x=df["x"], y=df["redeemed"], name="Redeemed",
        marker_color="#4A90D9"))
    budget_x = pd.concat([df["x"] - 0.5, pd.Series([df["x"].max() + 0.5])])
    budget_y = pd.concat([weekly_budget, pd.Series([weekly_budget.iloc[-1]])])
    fig.add_trace(go.Scatter(x=budget_x, y=budget_y, name="Budget/4",
        mode="lines", line=dict(dash="dot", color="red")))
    month_ticks = df.groupby("ym")["x"].first()
    month_budgets = df.groupby("ym").first()
    dollar_annotations = [dict(
        x=month_ticks[ym] - 0.5, y=float(month_budgets.loc[ym, "point_budget"]) / 4,
        text=f"${int(month_budgets.loc[ym, 'point_budget'] * month_budgets.loc[ym, 'conversion_rate'])} (${month_budgets.loc[ym, 'conversion_rate']:.0f}/pt)",
        showarrow=False, yshift=10, font=dict(color="red", size=10))
        for ym in month_ticks.index]
    month_lines = [dict(type="line", x0=x - 0.5, x1=x - 0.5, y0=0, y1=1, yref="paper",
        line=dict(dash="dot", color="grey", width=1)) for x in month_ticks.values[1:]]
    fig.update_layout(
        barmode="group", title="Weekly Kudos Acquired & Redeemed",
        annotations=dollar_annotations, shapes=month_lines,
        margin=dict(t=30, b=30),
        xaxis=dict(tickvals=month_ticks.values, ticktext=[
            pd.Timestamp(m + "-01").strftime("%b %Y") for m in month_ticks.index]),
        yaxis_title="Points", legend=dict(orientation="h", y=1, yanchor="top",
            bgcolor="rgba(255,255,255,0.7)"))
    return fig

def _add_forecast(fig, df):
    try:
        weeks_per_month = df.groupby("ym")["ym"].transform("size")
        irr_df, fc = fit_its(df[weeks_per_month >= 4])
        fx = df["x"].max() + 2
        fig.add_trace(go.Scatter(x=[fx], y=[fc["median"]], mode="markers",
            marker=dict(symbol="diamond", size=10, color="#4A90D9"),
            name="Forecast", error_y=dict(type="data", symmetric=False,
                array=[fc["hi"] - fc["median"]], arrayminus=[fc["median"] - fc["lo"]])))
        return irr_df
    except Exception:
        return pd.DataFrame()

def _build_irr_chart(irr_df):
    fig = go.Figure()
    if not irr_df.empty:
        fig.add_trace(go.Scatter(
            x=irr_df["month"], y=irr_df["irr"], mode="markers+lines",
            marker=dict(size=8, color="#50B86C"),
            text=[f"${r:.0f}/pt" for r in irr_df["rate"]],
            textposition="top center",
            error_y=dict(type="data", symmetric=False,
                array=(irr_df["hi"] - irr_df["irr"]).tolist(),
                arrayminus=(irr_df["irr"] - irr_df["lo"]).tolist())))
        fig.update_layout(title="IRR vs Previous Rate Over Time",
            margin=dict(t=40, b=30),
            xaxis_title="Month", yaxis_title="Incidence Rate Ratio")
    return fig

@callback(
    Output("snapshot", "children"),
    Output("usage-plot", "figure"),
    Output("irr-plot", "figure"),
    Input("snapshot", "id"))
def update_usage(_):
    budget, spent, overflow = load_snapshot()
    snap = [dbc.Col(dbc.Card(dbc.CardBody([
        html.H6(label, className="card-subtitle text-muted"),
        html.H3(f"{val} pts")]), className="shadow-sm"), md=4)
        for label, val in [("Monthly Budget", budget), ("Spent This Month", spent), ("Overflowed This Month", overflow)]]
    df = load_weekly()
    if df.empty:
        empty = go.Figure()
        return snap, empty, empty
    fig = _build_usage_chart(df)
    irr_df = _add_forecast(fig, df)
    return snap, fig, _build_irr_chart(irr_df)

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
    Output("leaderboard-label", "children"),
    Output("leaderboard-table", "columnDefs"),
    Output("leaderboard-table", "rowData"),
    Input("leaderboard-plot", "clickData"))
def drill_leaderboard(click):
    cols = [{"field": "giver", "flex": 1}, {"field": "recipient", "flex": 1},
        {"field": "message", "flex": 11}]
    if not click:
        return "Click a name to see messages.", cols, []
    name = click["points"][0]["y"]
    msgs = load_user_messages(name)
    return f"Messages for {name}", cols, msgs.to_dict("records") if not msgs.empty else []

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
    app.run(debug=os.environ.get("DASH_DEBUG", "").lower() in ("1", "true"))
