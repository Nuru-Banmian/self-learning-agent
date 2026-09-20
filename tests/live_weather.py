"""Opt-in public HTTP/SSE weather acceptance; mock weather is explicitly labelled."""

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_chat import submit
from tests.test_weather import city, forecast


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock-weather", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="assistant-weather-") as directory:
        settings = Settings(
            db_path=Path(directory) / "live.db", run_timeout_seconds=120
        )
        if not args.mock_weather and not (
            settings.qweather_api_key.get_secret_value() and settings.qweather_api_host
        ):
            print(
                "SKIP real QWeather: independent QWEATHER_API_KEY/API_HOST "
                "not configured; mainland network unverified"
            )
            return
        if not settings.dashscope_api_key.get_secret_value():
            print("SKIP real model: no local credential")
            return

        async def provider(request):
            if request.url.path == "/geo/v2/city/lookup":
                return httpx.Response(200, json={"code": "200", "location": [city()]})
            if request.url.path.startswith("/weather/v1/daily/"):
                return httpx.Response(
                    200,
                    json={
                        "metadata": {
                            "attributions": [
                                "https://developer.qweather.com/attribution.html"
                            ]
                        },
                        "days": [forecast()],
                    },
                )
            async with httpx.AsyncHTTPTransport() as real:
                response = await real.handle_async_request(request)
                await response.aread()
                return response

        options = {}
        if args.mock_weather:
            settings = settings.model_copy(
                update={"qweather_api_host": "fixture.qweatherapi.com"}
            )
            # The fixture receives no real weather credential.
            from pydantic import SecretStr

            settings.qweather_api_key = SecretStr("fixture-only")
            options = {
                "transport": httpx.MockTransport(provider),
                "clock": lambda: datetime(2026, 9, 20, 2, tzinfo=UTC),
            }
        with TestClient(create_app(settings, **options)) as client:
            started = monotonic()
            run, events = submit(client, "查询明天上海天气，出门需要准备什么？")
            print(
                json.dumps(
                    {
                        "mode": "real-model/mock-weather"
                        if args.mock_weather
                        else "real-model/real-weather",
                        "status": run["status"],
                        "weather": run["weather"],
                        "reply": run["reply"],
                        "elapsed_ms": round((monotonic() - started) * 1000),
                        "proxy": "trust_env=False",
                        "mainland_network": "unverified",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            assert "event: terminal" in events
            assert client.get("/api/todos").json() == []
            assert run["weather"]["status"] == "success"
            assert run["weather"]["forecast"]


if __name__ == "__main__":
    main()
