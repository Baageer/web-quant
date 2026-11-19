from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["service"] == "web-quant"


def test_market_overview_contract():
    response = client.get("/api/market/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "sample"
    assert "asOf" in body and "stale" in body and "warnings" in body

    data = body["data"]
    assert data["tradeDate"] == "20261118"
    assert len(data["indices"]) == 6
    assert "pctChg" in data["indices"][0]
    assert "limitUp" in data["breadth"]
    assert "medianPct" in data["breadth"]
    assert "ratio5d" in data["amount"]
    assert data["leaders"]["gainers"]
    assert data["regime"]["label"] in {"risk_on", "neutral", "risk_off"}


def test_market_tickers_pagination():
    response = client.get(
        "/api/market/tickers",
        params={"limit": 5, "offset": 0, "sort": "pctChg", "order": "desc"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 80
    assert len(data["items"]) == 5
    values = [item["pctChg"] for item in data["items"]]
    assert values == sorted(values, reverse=True)


def test_market_invalid_sort_returns_error_code():
    response = client.get("/api/market/tickers", params={"sort": "nope"})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "INVALID_PARAM"


def test_data_status_and_config():
    status = client.get("/api/data/status")
    assert status.status_code == 200
    body = status.json()
    assert body["mode"] == "offline"
    assert body["tokenConfigured"] is False
    assert any(item["dataset"] == "daily" for item in body["datasets"])

    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["latestTradeDate"] == "20261118"


def test_refresh_is_blocked_in_offline_mode():
    response = client.post("/api/data/refresh", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "MODE_NOT_ALLOWED"


def test_radar_opportunities_contract():
    response = client.get("/api/radar/opportunities", params={"topK": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "sample"

    data = body["data"]
    assert data["tradeDate"] == "20261118"
    assert len(data["items"]) == 10
    item = data["items"][0]
    for key in (
        "rank",
        "tsCode",
        "name",
        "close",
        "pctChg",
        "turnoverRate",
        "volumeRatio",
        "amount",
        "score",
        "confidence",
        "signal",
        "signalLabel",
        "riskLevel",
        "bias",
        "keyReasons",
        "riskFlags",
        "tradePlan",
    ):
        assert key in item
    assert "entryLow" in item["tradePlan"]
    assert "stopLoss" in item["tradePlan"]
    assert "positionPct" in item["tradePlan"]


def test_radar_detail_contract():
    first = client.get("/api/radar/opportunities", params={"topK": 1}).json()["data"][
        "items"
    ][0]
    response = client.get(f"/api/radar/{first['tsCode']}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tsCode"] == first["tsCode"]
    assert data["eligible"] is True
    assert len(data["factors"]) == 7
    assert "valueText" in data["factors"][0]
    assert "contribution" in data["factors"][0]


def test_radar_unknown_symbol():
    response = client.get("/api/radar/999999.SH")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "SYMBOL_NOT_FOUND"


def test_kline_contract():
    response = client.get(
        "/api/kline/600001.SH", params={"period": "daily", "adjust": "qfq", "limit": 120}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "sample"

    data = body["data"]
    assert data["tsCode"] == "600001.SH"
    assert data["period"] == "daily"
    assert data["adjust"] == "qfq"
    assert len(data["bars"]) == 120
    for key in ("date", "open", "high", "low", "close", "volume", "amount", "pctChg"):
        assert key in data["bars"][0]

    indicators = data["indicators"]
    for key in ("ma", "boll", "macd", "rsi", "volMa"):
        assert key in indicators
    assert len(indicators["ma"]["ma20"]) == 120
    assert len(indicators["macd"]["hist"]) == 120


def test_kline_unknown_symbol():
    response = client.get("/api/kline/999999.SH")
    assert response.status_code == 404
    assert response.json()["error"] == "SYMBOL_NOT_FOUND"


def test_signal_contract():
    response = client.get("/api/signal/600001.SH", params={"period": "daily"})
    assert response.status_code == 200
    data = response.json()["data"]
    for key in (
        "tsCode",
        "name",
        "tradeDate",
        "score",
        "signal",
        "signalLabel",
        "confidence",
        "direction",
        "marketState",
        "tradePlan",
        "scoreBreakdown",
        "evidence",
        "riskFlags",
        "constraints",
    ):
        assert key in data

    plan = data["tradePlan"]
    for key in (
        "entryLow",
        "entryHigh",
        "stopLoss",
        "target1",
        "target2",
        "rr1",
        "rr2",
        "positionPct",
        "executable",
        "executionNote",
        "shares",
    ):
        assert key in plan
    assert "technical" in data["evidence"]
    assert "capital" in data["evidence"]
    assert "sentiment" in data["evidence"]


def test_signal_batch_contract():
    response = client.get(
        "/api/signal/batch", params={"codes": "600001.SH,600002.SH"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["tradeDate"] == "20261118"
    assert len(data["items"]) == 2
    assert data["items"][0]["tsCode"] == "600001.SH"
    assert "executable" in data["items"][0]
