"""Dash dashboard tests using dash.testing (dash_duo fixture).

Seed data is loaded from a pg_dump fixture. To regenerate:
    uv run python -c "from simulate import main; main()"  # requires LLM servers
    pg_dump --no-owner --clean --if-exists $DATABASE_URL -f tests/fixtures/kudos_test.sql
"""
import os
import subprocess
from pathlib import Path

import chromedriver_autoinstaller
import pandas as pd
import pytest

DB_URL = os.environ.get("KUDOS_TEST_DATABASE_URL", "postgresql://localhost/kudos_test")
_DUMP = Path(__file__).resolve().parent / "fixtures" / "kudos_test.sql"


@pytest.fixture(scope="session", autouse=True)
def _chromedriver():
    chromedriver_autoinstaller.install()


@pytest.fixture(scope="module", autouse=True)
def seed():
    """Recreate schema and load data-only pg_dump fixture."""
    schema_dir = Path(__file__).resolve().parent.parent / "schema"
    sql_files = sorted(schema_dir.glob("*.sql"))
    cmds = ["psql", DB_URL, "-v", "ON_ERROR_STOP=1",
        "-c", "DROP SCHEMA IF EXISTS public CASCADE;",
        "-c", "CREATE SCHEMA public;"]
    for f in sql_files:
        cmds += ["-f", str(f)]
    cmds += [
        "-c", "SET session_replication_role = replica;",
        "-f", str(_DUMP),
        "-c", "SET session_replication_role = DEFAULT;"]
    subprocess.run(cmds, check=True)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    from dash_app import app
    return app


def test_snapshot_cards(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".card", timeout=10)
    cards = dash_duo.find_elements(".card")
    assert len(cards) == 3
    labels = [c.find_element("css selector", "h6").text for c in cards]
    assert "Monthly Budget" in labels
    assert "Spent This Month" in labels
    assert "Overflowed This Month" in labels
    for card in cards:
        assert "pts" in card.find_element("css selector", "h3").text

def test_spent_excludes_overflow(monkeypatch):
    """Spent count should match redeemed_this_month (which excludes overflow)."""
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    from dash_app import load_snapshot, scalar
    _, spent, _ = load_snapshot()
    redeemed = scalar("SELECT redeemed_this_month()")
    assert spent == (redeemed or 0)

def test_usage_plot(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element("#usage-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#usage-plot .js-plotly-plot .trace")
    assert len(traces) >= 3

def test_irr_plot(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element("#irr-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#irr-plot .js-plotly-plot .trace")
    assert len(traces) > 0

def test_irr_values(monkeypatch):
    """IRR CIs should be in [0.1, 5] and the first IRR should be highest."""
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    from dash_app import load_weekly, fit_its
    irr_df, _ = fit_its(load_weekly())
    assert len(irr_df) >= 2
    assert all(0.1 <= lo and hi <= 5 for lo, hi in zip(irr_df["lo"], irr_df["hi"]))
    assert irr_df["irr"].iloc[0] > irr_df["irr"].iloc[1:].mean()

def test_irr_empty_with_single_rate(monkeypatch):
    """fit_its returns empty DataFrame when there's only one conversion rate."""
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    from dash_app import fit_its
    df = pd.DataFrame({"yw": ["2026-01"], "ym": ["2026-01"], "acquired": [5],
        "redeemed": [3], "point_budget": [100], "conversion_rate": [1.0]})
    irr_df, fc = fit_its(df)
    assert irr_df.empty

def test_leaderboard(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".tab--selected", timeout=10)
    tabs = dash_duo.find_elements(".tab")
    tabs[1].click()
    dash_duo.wait_for_element("#leaderboard-plot .js-plotly-plot", timeout=10)
    bars = dash_duo.find_elements("#leaderboard-plot .trace.bars")
    assert len(bars) > 0
    # Click the top bar and verify messages appear
    from selenium.webdriver.common.action_chains import ActionChains
    bar = dash_duo.find_element("#leaderboard-plot .trace.bars .point")
    ActionChains(dash_duo.driver).move_to_element(bar).click().perform()
    dash_duo.wait_for_contains_text("#leaderboard-label", "Messages for", timeout=5)
    rows = dash_duo.find_elements("#leaderboard-table .ag-row")
    assert len(rows) > 0

def _has_clusters():
    import psycopg
    with psycopg.connect(DB_URL) as conn:
        return conn.execute("SELECT EXISTS(SELECT 1 FROM clusters)").fetchone()[0]

@pytest.mark.skipif("not _has_clusters()", reason="No cluster data (requires LLM backfill)")
def test_topic_streamgraph(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".tab--selected", timeout=10)
    tabs = dash_duo.find_elements(".tab")
    tabs[2].click()
    dash_duo.wait_for_element("#stream-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#stream-plot .js-plotly-plot .trace")
    assert len(traces) >= 2

@pytest.mark.skipif("not _has_clusters()", reason="No cluster data (requires LLM backfill)")
def test_topic_drilldown(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".tab--selected", timeout=10)
    tabs = dash_duo.find_elements(".tab")
    tabs[2].click()
    dash_duo.wait_for_element("#stream-plot .js-plotly-plot", timeout=10)
    from selenium.webdriver.common.action_chains import ActionChains
    plot = dash_duo.find_element("#stream-plot .js-plotly-plot .nsewdrag")
    ActionChains(dash_duo.driver).move_to_element(plot).click().perform()
    dash_duo.wait_for_contains_text("#topic-label", "Topic:", timeout=5)
    rows = dash_duo.find_elements("#topic-table .ag-row")
    assert len(rows) > 0
