"""Tool results sent to the LLM stay compact: summaries instead of raw series, slim PO rows."""
from app import db, scenarios
from app.agent import tools


def call(name, **a):
    with db.connect() as conn:
        return tools.DISPATCH[name](conn, a)


def test_demand_tool_returns_summaries_not_series():
    scenarios.load_scenario("S1-e")
    d = call("get_demand", sku="SKU-COLA-2L", node_id="N-BOG")
    assert "forecast_next_days" not in d and "actuals_last_14d" not in d
    assert d["forecast_total_next_7d"] == 420 and d["forecast_total_next_28d"] == 1680
    assert d["actual_last_7d_daily_avg"] == 110 and d["recent_vs_forecast_pct"] > 30


def test_coverage_and_open_po_rows_are_slim():
    scenarios.load_scenario("S2-a")
    cov = call("compute_coverage", sku="SKU-MILK-1L", node_id="N-MDE", supplier_id="S-PACIFICO")
    assert cov["inbound"] == 250 and cov["inbound_pos"][0]["expected_qty"] == 250
    assert "node_id" not in cov["inbound_pos"][0] and "latest_notice" in cov["inbound_pos"][0]
    pos = call("get_open_purchase_orders", sku="SKU-MILK-1L", node_id="N-MDE")
    assert set(pos[0]) <= set(tools.PO_FIELDS)
    assert len(tools.dumps(cov)) < 1500


def test_dumps_is_compact():
    assert tools.dumps({"a": [1, 2], "b": "x"}) == '{"a":[1,2],"b":"x"}'
