"""Tests for analytics helpers (trends, concentration, anomalies)."""

from datetime import date, timedelta

from dashboard import analytics


def _award(amount, recipient="X", agency="A", days_ago=0, award_id="id"):
    return {
        "Award ID": award_id,
        "Recipient Name": recipient,
        "Award Amount": amount,
        "Awarding Agency": agency,
        "Description": "some description text",
        "Action Date": (date.today() - timedelta(days=days_ago)).isoformat(),
    }


def test_basic_stats_handles_empty_input():
    stats = analytics.basic_stats([])
    assert stats["count"] == 0
    assert stats["total"] == 0
    assert stats["avg"] == 0
    assert stats["unique_recipients"] == 0


def test_basic_stats_aggregates_correctly():
    awards = [_award(100, "A"), _award(300, "A"), _award(200, "B")]
    stats = analytics.basic_stats(awards)
    assert stats["count"] == 3
    assert stats["total"] == 600
    assert stats["avg"] == 200
    assert stats["max"] == 300
    assert stats["min"] == 100


def test_daily_trend_buckets_recent_awards():
    awards = [_award(100, days_ago=0), _award(200, days_ago=0), _award(50, days_ago=2)]
    rows = analytics.daily_trend(awards, days=5)
    # Newest day first.
    assert rows[0]["date"] == date.today().isoformat()
    assert rows[0]["count"] == 2
    assert rows[0]["total"] == 300
    assert any(r["date"] == (date.today() - timedelta(days=2)).isoformat() for r in rows)


def test_concentration_computes_hhi_and_top_share():
    awards = [_award(100, "A"), _award(100, "B"), _award(800, "A")]
    block = analytics.concentration(awards, "Recipient Name", top_n=2)
    assert block["unique"] == 2
    assert block["total"] == 1000
    # A has 90%, B has 10% -> HHI = 90^2 + 10^2 = 8200
    assert abs(block["hhi"] - 8200) < 1
    assert block["top_share"] == 1.0  # both fit in top-2
    assert block["top"][0]["name"] == "A"


def test_repeat_recipients_filters_min_count():
    awards = [_award(100, "A"), _award(50, "A"), _award(200, "B")]
    rows = analytics.repeat_recipients(awards, min_awards=2)
    assert len(rows) == 1
    assert rows[0]["recipient"] == "A"
    assert rows[0]["awards"] == 2
    assert rows[0]["total"] == 150


def test_anomaly_flags_detects_outlier_and_missing_fields():
    awards = [
        _award(100, "A"),
        _award(105, "A"),
        _award(110, "A"),
        _award(95, "A"),
        _award(10_000_000, "Outlier"),  # huge outlier
        {
            "Award ID": "x",
            "Recipient Name": "",  # missing
            "Award Amount": 100,
            "Awarding Agency": "",  # missing
            "Description": "",  # too short
            "Action Date": date.today().isoformat(),
        },
    ]
    flags = analytics.anomaly_flags(awards, z_threshold=2.0)
    flagged_ids = [f.get("award_id") for f in flags]
    # Outlier and the bad-data award both flagged.
    assert any("Outlier" == f["recipient"] for f in flags)
    assert any("missing recipient name" in r for f in flags for r in f["reasons"])


def test_trend_deltas_returns_empty_without_baseline():
    assert analytics.trend_deltas({"count": 5, "total": 100, "avg": 20}, None) == {}


def test_trend_deltas_computes_pct_change():
    deltas = analytics.trend_deltas(
        {"count": 12, "total": 200, "avg": 16},
        {"count": 10, "total": 100, "avg": 10},
    )
    assert deltas["count"]["diff"] == 2
    assert deltas["total"]["pct"] == 100.0
