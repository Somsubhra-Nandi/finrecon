"""Production FastAPI static serving must preserve BrowserRouter deep links."""

from pathlib import Path

from fastapi.testclient import TestClient

import finrecon.api.app as app_module


def test_static_spa_fallback_serves_all_workspace_routes_without_swallowing_api(tmp_path: Path, monkeypatch):
    dist = tmp_path / "web" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>', encoding="utf-8")
    (assets / "app.js").write_text("console.log('asset')", encoding="utf-8")
    monkeypatch.setattr(app_module, "PROJECT_ROOT", tmp_path)

    with TestClient(app_module.create_app(ledger_path=tmp_path / "var" / "ledger.sqlite3")) as client:
        for route in ("/", "/overview", "/reconciliation", "/source-issues", "/benchmarks"):
            response = client.get(route)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert 'id="root"' in response.text

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("text/javascript")
        assert client.get("/api/not-a-route").status_code == 404
