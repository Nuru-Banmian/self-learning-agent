import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings
from tests.test_maintenance import operation_response


def submit(client, content, request_id="request-1", session=None):
    session = session or client.post("/api/sessions").json()["id"]
    assert (
        client.post(
            f"/api/sessions/{session}/messages",
            json={"request_id": request_id, "content": content},
        ).status_code
        == 202
    )
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{request_id}").json()
        if run["status"] != "running":
            return run, client.get(f"/api/runs/{request_id}/events").text
        time.sleep(0.01)
    raise AssertionError("public run did not terminate")


def city(name="上海", **changes):
    return {
        "id": "101020100",
        "name": name,
        "lat": "31.23",
        "lon": "121.47",
        "tz": "Asia/Shanghai",
        "adm1": "上海市",
        "adm2": "上海",
        "country": "中国",
        **changes,
    }


def forecast(start="2026-09-21T00:00+08:00", end="2026-09-22T00:00+08:00"):
    period = {
        "condition": {"text": "小雨", "code": "305"},
        "precipitation": {"amount": {"value": 2, "unit": "mm"}, "probability": 0.7},
        "wind": {"speed": {"value": 3, "unit": "m/s"}},
    }
    return {
        "forecastStartTime": start,
        "forecastEndTime": end,
        "temperatureMin": {"value": 18, "unit": "°C"},
        "temperatureMax": {"value": 24, "unit": "°C"},
        "daytime": period,
        "nighttime": period,
    }


def weather_app(
    tmp_path,
    requests,
    *,
    task=None,
    locations=None,
    days=None,
    weather=None,
    **settings,
):
    async def provider(request):
        requests.append(request)
        if request.url.path == "/geo/v2/city/lookup":
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "location": locations(request)
                    if callable(locations)
                    else locations
                    if locations is not None
                    else [city()],
                },
            )
        if request.url.path.startswith("/weather/v1/daily/"):
            if weather:
                return await weather(request)
            return httpx.Response(
                200,
                json={
                    "metadata": {
                        "attributions": [
                            "https://developer.qweather.com/attribution.html"
                        ]
                    },
                    "days": days(request)
                    if callable(days)
                    else days
                    if days is not None
                    else [forecast()],
                },
            )
        body = json.loads(request.content)
        if body["messages"][-1]["content"] == "请记录明天去上海办事":
            return httpx.Response(
                200,
                json=operation_response(
                    "create_todos",
                    {"items": [{"title": "去上海办事", "date_text": "明天"}]},
                ),
            )
        if "response_format" in body:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": '{"candidates": []}'}}]}
            )
        selected_task = task(body) if callable(task) else task
        return httpx.Response(
            200,
            json=operation_response(
                "prepare_outing",
                selected_task
                or {"destination": "上海", "date_text": "明天", "todo_id": None},
            ),
        )

    config = Settings(
        _env_file=None,
        db_path=tmp_path / "weather.db",
        dashscope_api_key="model-only",
        **(
            {
                "qweather_api_key": "weather-only",
                "qweather_api_host": "test.qweatherapi.com",
            }
            | settings
        ),
    )
    return create_app(
        config,
        transport=httpx.MockTransport(provider),
        clock=lambda: datetime(2026, 9, 20, 2, tzinfo=UTC),
    )


def test_weather_uses_destination_date_and_returns_evidence_without_writes(tmp_path):
    requests = []
    with TestClient(weather_app(tmp_path, requests)) as c:
        before = c.get("/api/todos").json()
        run, events = submit(c, "查询明天上海天气，出门要准备什么？")
        assert run["status"] == "completed"
        assert run["weather"]["status"] == "success"
        assert run["weather"]["date"] == "2026-09-21"
        assert run["weather"]["location"]["tz"] == "Asia/Shanghai"
        assert run["weather"]["forecast"]["temp_min"] == 18
        assert run["weather"]["queried_at"]
        assert "雨具" in run["reply"]
        assert "event: terminal" in events and '"tool": "qweather_daily"' in events
        assert c.get("/api/todos").json() == before
        external = [r for r in requests if r.method == "GET"]
        assert [r.url.path for r in external] == [
            "/geo/v2/city/lookup",
            "/weather/v1/daily/31.23/121.47",
        ]
        assert all(r.headers["X-QW-Api-Key"] == "weather-only" for r in external)
        assert all(
            "range" not in r.url.params and "key" not in r.url.params for r in external
        )
        assert "weather-only" not in events + json.dumps(run)


@pytest.mark.parametrize(
    "task,content",
    [
        (
            {"destination": None, "date_text": "明天", "todo_id": None},
            "明天出门天气怎么样？",
        ),
        (
            {"destination": "上海", "date_text": "明天", "todo_id": None},
            "明天出门天气怎么样？",
        ),
        (
            {"destination": "上海", "date_text": None, "todo_id": None},
            "上海天气怎么样？",
        ),
        (
            {"destination": "上海", "date_text": "明天", "todo_id": None},
            "查询今天上海天气",
        ),
    ],
)
def test_weather_does_not_guess_missing_or_unsupported_destination_and_date(
    tmp_path, task, content
):
    requests = []
    with TestClient(weather_app(tmp_path, requests, task=task)) as c:
        run, events = submit(c, content)
        assert run["weather"]["status"] == "empty"
        assert "请" in run["reply"]
        assert not [r for r in requests if r.method == "GET"]
        assert "event: terminal" in events


def test_existing_outing_date_is_preserved_and_weather_suggestions_require_acceptance(
    tmp_path,
):
    from tests.test_maintenance import action

    target = {}
    with TestClient(
        weather_app(
            tmp_path,
            [],
            task=lambda _: {
                "destination": "上海",
                "date_text": None,
                "todo_id": target["id"],
            },
        )
    ) as c:
        created, _ = submit(c, "请记录明天去上海办事", "create")
        target["id"] = created["todo_ids"][0]
        before = c.get("/api/todos").json()
        run, _ = submit(c, "去上海办事需要准备什么？", "weather")
        assert run["weather"]["status"] == "success"
        assert run["weather"]["date"] == "2026-09-21"
        assert c.get("/api/todos").json() == before
        session = run["session_id"]
        suggestion = c.get(f"/api/sessions/{session}/suggestions").json()[0]
        for rid in ("accept", "accept", "accept-again"):
            accepted, _ = action(
                c,
                session,
                rid,
                "accept_suggestion",
                {"suggestion_id": suggestion["id"]},
            )
            assert accepted["status"] == "completed"
        todos = c.get("/api/todos").json()
        assert len(todos) == 2 and todos[1]["scheduled_date"] == "2026-09-21"


def test_relative_query_date_uses_destination_timezone_not_user_timezone(tmp_path):
    locations = [
        city(
            "洛杉矶",
            id="US1",
            tz="America/Los_Angeles",
            lat="34.05",
            lon="-118.24",
            country="美国",
            adm1="加利福尼亚",
            adm2="洛杉矶",
        )
    ]
    days = [forecast("2026-09-20T00:00-07:00", "2026-09-21T00:00-07:00")]
    task = {"destination": "洛杉矶", "date_text": "明天", "todo_id": None}
    with TestClient(
        weather_app(tmp_path, [], task=task, locations=locations, days=days)
    ) as c:
        run, _ = submit(c, "查询明天洛杉矶天气")
        assert run["weather"]["date"] == "2026-09-20"
        assert run["weather"]["status"] == "success"


def test_ambiguous_city_requires_full_confirmed_query_without_fetching_forecast(
    tmp_path,
):
    requests = []
    locations = [
        city("朝阳", id="101071201", adm1="辽宁省"),
        city("朝阳", id="101010300", adm1="北京市"),
    ]
    task = {"destination": "朝阳", "date_text": "明天", "todo_id": None}
    with TestClient(
        weather_app(tmp_path, requests, task=task, locations=locations)
    ) as c:
        run, _ = submit(c, "明天朝阳天气怎么样？")
        assert run["weather"]["status"] == "empty"
        assert len(run["weather"]["candidates"]) == 2
        assert "辽宁省" in run["reply"] and "101010300" in run["reply"]
        assert len([r for r in requests if r.method == "GET"]) == 1


@pytest.mark.parametrize(
    "days,date_text,expected,gap",
    [
        ([], "明天", "empty", "无数据"),
        (
            [forecast("2026-09-20T00:00+08:00", "2026-09-21T00:00+08:00")],
            "明天",
            "empty",
            "无数据",
        ),
        ([forecast()], "2026-10-20", "empty", "范围"),
        (
            [forecast() | {"temperatureMax": {"value": "NaN", "unit": "°C"}}],
            "明天",
            "partial",
            "无效",
        ),
    ],
)
def test_missing_wrong_date_out_of_range_or_invalid_forecast_is_not_used(
    tmp_path, days, date_text, expected, gap
):
    with TestClient(
        weather_app(
            tmp_path,
            [],
            days=days,
            task={"destination": "上海", "date_text": date_text, "todo_id": None},
        )
    ) as c:
        run, events = submit(c, f"查询{date_text}上海天气")
        assert run["weather"]["status"] == expected
        assert run["weather"]["forecast"] is None
        assert gap in run["reply"]
        assert c.get(f"/api/sessions/{run['session_id']}/suggestions").json() == []
        assert "event: terminal" in events


@pytest.mark.parametrize(
    "mode,settings,expected_calls",
    [
        ("permission", {}, 2),
        ("retry", {}, 3),
        ("budget", {"max_weather_calls": 1}, 1),
        ("timeout", {"weather_timeout_seconds": 0.03}, 3),
        ("deadline", {"run_timeout_seconds": 0.08}, 2),
    ],
)
def test_weather_failures_are_bounded_preserve_location_and_finish_events(
    tmp_path, mode, settings, expected_calls
):
    async def failure(request):
        if mode in ("timeout", "deadline"):
            await asyncio.sleep(1)
        return httpx.Response(403 if mode == "permission" else 503, json={})

    requests = []
    with TestClient(weather_app(tmp_path, requests, weather=failure, **settings)) as c:
        run, events = submit(c, "查询明天上海天气")
        record = run["weather"]
        assert record["status"] == "partial"
        assert record["location"]["name"] == "上海" and record["forecast"] is None
        assert len([r for r in requests if r.method == "GET"]) == expected_calls
        assert all(call["status"] != "running" for call in record["calls"])
        assert "event: terminal" in events
        assert '"role": "execution", "status": "partial"' in events
        assert c.get("/api/todos").json() == []


def test_fuzzy_single_city_is_not_silently_treated_as_confirmed_destination(tmp_path):
    requests = []
    with TestClient(
        weather_app(tmp_path, requests, locations=[city("上杭", id="101230705")])
    ) as c:
        run, _ = submit(c, "查询明天上海天气")
        assert run["weather"]["status"] == "empty"
        assert run["weather"]["location"] is None
        assert len([r for r in requests if r.method == "GET"]) == 1


def test_provider_data_unavailable_is_empty_not_generic_http_failure(tmp_path):
    async def unavailable(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "status": 400,
                    "type": "https://dev.qweather.com/docs/resource/error-code/#data-not-available",
                    "detail": "private-provider-detail",
                }
            },
        )

    with TestClient(weather_app(tmp_path, [], weather=unavailable)) as c:
        run, events = submit(c, "查询明天上海天气")
        assert run["weather"]["status"] == "empty"
        assert "无数据" in run["reply"]
        assert "private-provider-detail" not in events


@pytest.mark.parametrize(
    "locations", [[], [city(tz="Not/AZone")], [city(lat="nan")], [None]]
)
def test_unconfirmed_provider_location_cannot_query_weather(tmp_path, locations):
    requests = []
    with TestClient(weather_app(tmp_path, requests, locations=locations)) as c:
        run, events = submit(c, "查询明天上海天气")
        assert run["weather"]["status"] in ("empty", "error")
        assert run["weather"]["forecast"] is None
        assert len([r for r in requests if r.method == "GET"]) == 1
        assert "event: terminal" in events


def test_weather_and_replay_survive_reopen_without_credentials(tmp_path):
    requests = []
    app = weather_app(tmp_path, requests)
    with TestClient(app) as c:
        run, events = submit(c, "查询明天上海天气", "durable")
    with TestClient(
        create_app(Settings(_env_file=None, db_path=tmp_path / "weather.db"))
    ) as c:
        replay, replay_events = submit(
            c, "查询明天上海天气", "durable", run["session_id"]
        )
        assert replay == run and replay_events == events
        assert c.get(f"/api/sessions/{run['session_id']}/suggestions").json()


def test_missing_weather_configuration_does_not_reuse_model_credential(tmp_path):
    requests = []
    with TestClient(weather_app(tmp_path, requests, qweather_api_key="")) as c:
        run, events = submit(c, "查询明天上海天气")
        assert run["weather"]["status"] == "error"
        assert run["weather"]["calls"] == []
        assert not [r for r in requests if r.method == "GET"]
        assert "独立项目凭据" in run["reply"] and "model-only" not in events


def test_explicit_weather_request_limits_main_to_readonly_weather_delegation(tmp_path):
    requests = []
    with TestClient(weather_app(tmp_path, requests)) as c:
        submit(c, "明天上海天气怎么样？")
    main = json.loads(next(r.content for r in requests if r.method == "POST"))
    assert main["tool_choice"] == {
        "type": "function",
        "function": {"name": "prepare_outing"},
    }
    assert [t["function"]["name"] for t in main["tools"]] == ["prepare_outing"]
