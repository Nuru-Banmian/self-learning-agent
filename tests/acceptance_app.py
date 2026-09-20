"""Live model; optionally simulated information providers. Isolated DB required."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import SecretStr

from app.main import create_app
from app.settings import Settings
from tests.test_weather import city, forecast


def create_acceptance_app():
    settings = Settings(db_path=Path(os.environ["DB_PATH"]))
    mocked = os.environ.get("ACCEPTANCE_MOCK_INFORMATION") == "1"
    if mocked:
        settings.iqs_api_key = SecretStr("simulated-iqs")
        settings.qweather_api_key = SecretStr("simulated-weather")
        settings.qweather_api_host = "fixture.qweatherapi.com"

    async def provider(request):
        if mocked and request.url.path == "/search/unified":
            query = json.loads(request.content)["query"]
            video = "视频" in query
            return httpx.Response(
                200,
                json={
                    "pageItems": [
                        {
                            "title": "Python 视频教程" if video else "Python 官方教程",
                            "link": "https://www.bilibili.com/video/BVfixture"
                            if video
                            else "https://docs.python.org/zh-cn/3/tutorial/classes.html",
                            "snippet": "示例演示 Python 生成器与装饰器，"
                            "先阅读或观看示例再编写练习。",
                        }
                    ]
                },
            )
        if mocked and request.url.path == "/readpage/basic":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "statusCode": 200,
                        "text": "Generators use yield; decorators wrap functions.",
                    }
                },
            )
        if mocked and request.url.path == "/geo/v2/city/lookup":
            location = request.url.params["location"]
            cities = [city()]
            if location == "朝阳":
                cities = [
                    city("朝阳", id="101071201", adm1="辽宁省"),
                    city("朝阳", id="101010300", adm1="北京市"),
                ]
            elif location == "101071201":
                cities = [city("朝阳", id=location, adm1="辽宁省")]
            elif location == "失败城":
                return httpx.Response(403)
            return httpx.Response(200, json={"code": "200", "location": cities})
        if mocked and request.url.path.startswith("/weather/v1/daily/"):
            return httpx.Response(
                200,
                json={
                    "days": [forecast()],
                    "metadata": {
                        "attributions": [
                            "https://developer.qweather.com/attribution.html"
                        ],
                    },
                },
            )
        # Record request shape only; never headers, credentials or provider errors.
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            audit = os.environ.get("ACCEPTANCE_AUDIT_PATH")
            if audit:
                with Path(audit).open("a", encoding="utf-8") as file:
                    file.write(
                        json.dumps(
                            {
                                "model": body["model"],
                                "mode": "json_schema"
                                if "response_format" in body
                                else "tool_calling",
                                "roles": [m["role"] for m in body["messages"]],
                            }
                        )
                        + "\n"
                    )
        async with httpx.AsyncHTTPTransport() as real:
            response = await real.handle_async_request(request)
            await response.aread()
            return response

    return create_app(
        settings,
        transport=httpx.MockTransport(provider),
        clock=(lambda: datetime(2026, 9, 20, 2, tzinfo=UTC)) if mocked else None,
    )
