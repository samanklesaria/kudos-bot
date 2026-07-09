"""Dash dashboard tests using dash.testing (dash_duo fixture).

Requires: LLM servers running (CHAT_URI, EMBEDDING_URI) for simulate.py backfill.
"""
import os
import subprocess

import chromedriver_autoinstaller
import pytest

chromedriver_autoinstaller.install()

DB_URL = os.environ.get("KUDOS_TEST_DATABASE_URL", "postgresql://localhost/kudos_test")


@pytest.fixture(scope="module", autouse=True)
def seed():
    """Run simulate.py to populate the test DB with realistic data."""
    subprocess.run(
        ["uv", "run", "python", "-c", "from simulate import main; main()"],
        env={**os.environ, "DATABASE_URL": DB_URL}, check=True)


@pytest.fixture
def app():
    os.environ["DATABASE_URL"] = DB_URL
    from dash_app import app
    return app


# §6 Operational snapshot
def test_snapshot_cards(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".card", timeout=10)
    cards = dash_duo.find_elements(".card")
    assert len(cards) == 3
    labels = [c.find_element("css selector", "h6").text for c in cards]
    assert "Monthly Budget" in labels
    assert "Spent This Month" in labels
    assert "Queued" in labels
    for card in cards:
        assert "pts" in card.find_element("css selector", "h3").text

# §6 Kudos sent and spent over time
def test_usage_plot(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element("#usage-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#usage-plot .js-plotly-plot .trace")
    assert len(traces) >= 3

# §6 Treatment effect estimation
def test_irr_plot(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element("#irr-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#irr-plot .js-plotly-plot .trace")
    assert len(traces) > 0

# §6 Distribution of points across recipients
def test_leaderboard(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".tab--selected", timeout=10)
    tabs = dash_duo.find_elements(".tab")
    tabs[1].click()
    dash_duo.wait_for_element("#leaderboard-plot .js-plotly-plot", timeout=10)
    bars = dash_duo.find_elements("#leaderboard-plot .trace.bars")
    assert len(bars) > 0

# §6 Kudos message themes — streamgraph
def test_topic_streamgraph(dash_duo, app):
    dash_duo.start_server(app)
    dash_duo.wait_for_element(".tab--selected", timeout=10)
    tabs = dash_duo.find_elements(".tab")
    tabs[2].click()
    dash_duo.wait_for_element("#stream-plot .js-plotly-plot", timeout=10)
    traces = dash_duo.find_elements("#stream-plot .js-plotly-plot .trace")
    assert len(traces) >= 2

# §6 Kudos message themes — drill-down
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
