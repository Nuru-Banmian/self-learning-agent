"""Isolated browser fixture: all providers simulated; no personal database access.

Run: python -m uvicorn tests.weather_demo:app --port 8017
"""

import tempfile
from pathlib import Path

import httpx

from tests.test_weather import city, forecast, weather_app


def task(body):
    content = body["messages"][-1]["content"]
    destination = next(
        (
            n
            for n in (
                "101071201",
                "朝阳",
                "洛杉矶",
                "无数据城",
                "失败城",
                "无名城",
                "上海",
            )
            if n in content
        ),
        None,
    )
    return {
        "destination": destination,
        "date_text": "2026-10-20" if "2026-10-20" in content else "明天",
        "todo_id": None,
    }


def locations(request):
    name = request.url.params["location"]
    if name == "朝阳":
        return [
            city("朝阳", id="101071201", adm1="辽宁省"),
            city("朝阳", id="101010300", adm1="北京市"),
        ]
    if name == "101071201":
        return [city("朝阳", id="101071201", adm1="辽宁省")]
    if name == "洛杉矶":
        return [
            city(
                "洛杉矶",
                id="US1",
                lat="34.05",
                lon="-118.24",
                tz="America/Los_Angeles",
                country="美国",
                adm1="加利福尼亚",
                adm2="洛杉矶",
            )
        ]
    if name == "无名城":
        return []
    return [city(name, lat={"无数据城": "1", "失败城": "2"}.get(name, "31.23"))]


async def weather(request):
    path = request.url.path
    if "/2.0/" in path:
        return httpx.Response(403)
    days = [forecast()]
    if "/1.0/" in path:
        days = []
    elif "/34.05/" in path:
        days = [forecast("2026-09-20T00:00-07:00", "2026-09-21T00:00-07:00")]
    return httpx.Response(
        200,
        json={
            "days": days,
            "metadata": {
                "attributions": ["https://developer.qweather.com/attribution.html"]
            },
        },
    )


app = weather_app(
    Path(tempfile.mkdtemp(prefix="weather-browser-")),
    [],
    task=task,
    locations=locations,
    weather=weather,
)
