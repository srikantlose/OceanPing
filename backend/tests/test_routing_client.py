import httpx
import pytest

from app.modules.routing import client
from app.modules.routing.client import RoutingUnavailable


def test_route_posts_locations_and_costing(monkeypatch):
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"trip": {"legs": [{"shape": "abc"}]}}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    result = client.route([{"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}], costing="pedestrian")

    assert captured["url"].endswith("/route")
    assert captured["json"]["costing"] == "pedestrian"
    assert captured["json"]["locations"] == [{"lat": 1.0, "lon": 2.0}, {"lat": 3.0, "lon": 4.0}]
    assert "exclude_polygons" not in captured["json"]
    assert result["trip"]["legs"][0]["shape"] == "abc"


def test_route_includes_exclude_polygons_when_given(monkeypatch):
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"trip": {"legs": []}}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    polygons = [[[80.0, 13.0], [80.1, 13.0], [80.1, 13.1], [80.0, 13.0]]]
    client.route([{"lat": 1.0, "lon": 2.0}], exclude_polygons=polygons)
    assert captured["json"]["exclude_polygons"] == polygons


def test_route_raises_routing_unavailable_on_http_error(monkeypatch):
    def _fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(400, json={"error": "no path found"}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(RoutingUnavailable):
        client.route([{"lat": 1.0, "lon": 2.0}])


def test_route_raises_routing_unavailable_on_connection_error(monkeypatch):
    def _fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(RoutingUnavailable):
        client.route([{"lat": 1.0, "lon": 2.0}])


def test_route_raises_routing_unavailable_when_no_trip_in_response(monkeypatch):
    def _fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"error": "no path could be found for input"}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    with pytest.raises(RoutingUnavailable):
        client.route([{"lat": 1.0, "lon": 2.0}])
