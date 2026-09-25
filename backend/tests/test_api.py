# -*- coding: utf-8 -*-
"""API 回归测试（pytest）。运行：python -m pytest tests/test_api.py -v"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    j = client.get("/api/v1/health").json()
    assert j["code"] == 0
    assert j["data"]["status"] == "up"


def test_meta():
    j = client.get("/api/v1/meta").json()
    assert j["code"] == 0
    assert j["data"]["industryCount"]["l2"] == 134
    assert j["data"]["stockCount"] > 5000


def test_industries_search():
    j = client.get("/api/v1/industries", params={"level": 2, "q": "半导体"}).json()
    assert j["code"] == 0
    assert j["data"]["total"] == 1
    assert j["data"]["list"][0]["name"] == "半导体"


def test_industry_detail():
    j = client.get("/api/v1/industries/801081").json()
    assert j["code"] == 0
    assert j["data"]["l1Name"] == "电子"


def test_industry_members():
    j = client.get("/api/v1/industries/801081/members", params={"as_of": "2026-09-24"}).json()
    assert j["code"] == 0
    assert j["data"]["total"] == 180


def test_industry_flow():
    j = client.get("/api/v1/industries/801081/flow").json()
    assert j["code"] == 0
    assert len(j["data"]["series"]) > 0
    assert "net" in j["data"]["series"][-1]


def test_stock_flow():
    j = client.get("/api/v1/stocks/600519.SH/flow").json()
    assert j["code"] == 0
    assert j["data"]["name"] == "贵州茅台"
    assert len(j["data"]["series"]) > 0


def test_search_maotai():
    j = client.get("/api/v1/stocks/search", params={"q": "茅台"}).json()
    assert j["code"] == 0
    assert j["data"]["list"][0]["name"] == "贵州茅台"
    assert j["data"]["list"][0]["industry"] == "白酒Ⅱ"


def test_404_industry():
    j = client.get("/api/v1/industries/999999").json()
    assert j["code"] == 40401


def test_admin_fetch_requires_key():
    j = client.post("/api/v1/admin/fetch", headers={"X-Admin-Key": "wrong"}).json()
    assert j["code"] == 40102
