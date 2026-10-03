"""Flask 接口测试（全部离线路径，不访问 YouTube）。"""

from __future__ import annotations

import io

import pytest

import app as app_module
from config import DEFAULT_TEST_URL


@pytest.fixture(scope="module")
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


class TestIndex:
    def test_renders(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "西语 YouTube 学习助手" in html
        assert "app.js" in html
        assert 'id="player-frame"' in html
        assert "rKhPeDyKJ1g" in html
        for level in ("A1", "B1", "C2"):
            assert level in html


class TestHealth:
    def test_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["glossary"]["available"] is True
        assert body["glossary"]["entries"] > 0
        assert isinstance(body["glossary"]["sources"], list)
        assert body["level_data"]["available"] is True
        assert body["demo_available"] is True
        assert body["llm"]["configured"] is False
        assert body["default_url"] == DEFAULT_TEST_URL
        assert [lv["level"] for lv in body["levels"]] == ["A1", "A2", "B1", "B2", "C1", "C2"]

    def test_no_secret_in_response(self, client, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-should-never-leak")
        body = client.get("/api/health").get_json()
        assert "sk-should-never-leak" not in str(body)


class TestDemo:
    @pytest.mark.parametrize("level", ["A1", "A2", "B1", "B2", "C1", "C2"])
    def test_all_levels(self, client, level):
        resp = client.get(f"/api/demo?level={level}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["level"] == level
        assert body["video"]["is_demo"] is True
        assert body["stats"]["sentences"] > 0

    def test_invalid_level_falls_back(self, client):
        body = client.get("/api/demo?level=Z9").get_json()
        assert body["level"] in ("A1", "A2", "B1", "B2", "C1", "C2")

    def test_zero_network_requests(self, client):
        """示例接口必须完全不碰网络：即使断网也应可用。"""
        resp = client.get("/api/demo?level=B1")
        assert resp.status_code == 200


class TestMaterial:
    def test_missing_url(self, client):
        resp = client.post("/api/material", json={"url": "", "level": "B1"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error"] is True
        assert body["error_code"] == "missing_url"
        assert body["message"] and body["hint"]

    def test_invalid_url(self, client):
        resp = client.post("/api/material", json={"url": "https://vimeo.com/1", "level": "B1"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["error_code"] == "invalid_url"

    def test_no_json_body(self, client):
        resp = client.post("/api/material", data="", content_type="application/json")
        assert resp.status_code == 400


class TestUpload:
    def _post(self, client, content=b"", filename="x.srt", level="B1", url=""):
        return client.post(
            "/api/material/upload",
            data={
                "level": level,
                "url": url,
                "subtitle": (io.BytesIO(content), filename),
            },
            content_type="multipart/form-data",
        )

    def test_no_file(self, client):
        resp = client.post(
            "/api/material/upload",
            data={"level": "B1"},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        assert resp.get_json()["error_code"] == "missing_file"

    def test_empty_file(self, client):
        resp = self._post(client)
        assert resp.status_code == 400
        assert resp.get_json()["error_code"] == "empty_file"

    def test_invalid_subtitle(self, client):
        resp = self._post(client, content=b"not a subtitle at all")
        assert resp.status_code == 400
        assert resp.get_json()["error_code"] == "invalid_subtitle"

    def test_valid_srt(self, client):
        srt = (
            "1\n00:00:01,000 --> 00:00:03,000\nHola, me llamo Ana.\n"
            "2\n00:00:03,500 --> 00:00:06,000\nVivo en Valencia.\n"
        ).encode("utf-8")
        resp = self._post(client, content=srt, filename="a.srt", level="A1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["source"] == "import"
        assert body["video"]["subtitle_filename"] == "a.srt"
        assert body["video"]["has_player"] is False
        assert body["stats"]["sentences"] >= 2

    def test_valid_vtt_with_url(self, client):
        vtt = (
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHola.\n\n"
            "00:00:03.500 --> 00:00:06.000\nVivo en Valencia.\n"
        ).encode("utf-8")
        resp = self._post(
            client, content=vtt, filename="a.vtt", level="B2", url=DEFAULT_TEST_URL
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["video"]["video_id"] == "rKhPeDyKJ1g"
        assert body["video"]["start_seconds"] == 123


class TestErrorShape:
    def test_all_errors_have_code_message_hint(self, client):
        for request in (
            lambda: client.post("/api/material", json={"url": ""}),
            lambda: client.post("/api/material", json={"url": "https://vimeo.com/1"}),
            lambda: client.post("/api/material/upload", data={},
                                content_type="multipart/form-data"),
        ):
            body = request().get_json()
            assert body["error"] is True
            assert body["error_code"]
            assert body["message"]
            assert "hint" in body
