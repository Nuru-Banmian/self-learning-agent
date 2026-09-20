"""Read-only outing preparation; provider facts never grant todo-write authority."""

import asyncio
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.iqs import public_url
from app.settings import Settings
from app.store import Store
from app.todos import (
    DATE_TOKEN,
    Clarification,
    date_from_text,
    overview,
    render_overview,
)


class WeatherTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: str | None = Field(max_length=100)
    date_text: str | None = Field(max_length=40)
    todo_id: str | None


def explicit_weather(content: str) -> bool:
    excluded = re.search(
        r"不要|不用|不需要|无需|别|记录|加入|修改|完成|删除|"
        r"接口|API|原理|方法|工具|收费|费用|怎么查|如何查|是什么|[‘’“”\"']",
        content,
        re.I,
    )
    return bool(
        "天气" in content
        and not excluded
        and (
            re.search(r"查|天气(?:怎么样|如何|怎样)|[？?]", content)
            or (DATE_TOKEN.search(content) and content.rstrip().endswith("天气"))
        )
    )


class QWeather:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        run_id: str,
        record: dict[str, Any],
        transport: httpx.AsyncBaseTransport | None,
    ):
        self.settings, self.store, self.run_id = settings, store, run_id
        self.record, self.transport = record, transport

    async def request(
        self, tool: str, path: str, params: dict[str, str]
    ) -> dict[str, Any] | None:
        key = self.settings.qweather_api_key.get_secret_value()
        if not key or not self.settings.qweather_api_host:
            self.record["gaps"].append(
                "和风未配置独立项目凭据或账户专属 API Host，未执行查询。"
            )
            return None
        for attempt in range(self.settings.weather_retries + 1):
            if len(self.record["calls"]) >= self.settings.max_weather_calls:
                self.record["gaps"].append("已达到城市与天气查询的调用次数上限。")
                return None
            call: dict[str, Any] = {
                "tool": tool,
                "input": {"path": path, **params},
                "status": "running",
                "attempt": attempt + 1,
            }
            self.record["calls"].append(call)
            self.store.weather_record(self.run_id, self.record)
            self.store.event(self.run_id, "tool_call", {"role": "execution", **call})
            started = monotonic()
            retry = False
            result = None
            try:
                async with asyncio.timeout(self.settings.weather_timeout_seconds):
                    async with httpx.AsyncClient(
                        transport=self.transport,
                        trust_env=False,
                        timeout=self.settings.weather_timeout_seconds,
                    ) as client:
                        async with client.stream(
                            "GET",
                            "https://" + self.settings.qweather_api_host + path,
                            headers={"X-QW-Api-Key": key},
                            params=params,
                        ) as response:
                            call["http_status"] = response.status_code
                            if response.status_code != 400:
                                response.raise_for_status()
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > 512_000:
                                    raise ValueError("oversized response")
                            parsed = json.loads(content)
                            if not isinstance(parsed, dict):
                                raise ValueError("invalid response")
                            if response.status_code == 400:
                                error = parsed.get("error", {})
                                kind = (
                                    error.get("type", "")
                                    if isinstance(error, dict)
                                    else ""
                                )
                                if kind in (
                                    "https://dev.qweather.com/docs/resource/error-code/#data-not-available",
                                    "https://dev.qweather.com/docs/resource/error-code/#no-such-location",
                                ):
                                    parsed = (
                                        {"code": "404"}
                                        if tool == "qweather_city"
                                        else {"days": []}
                                    )
                                else:
                                    response.raise_for_status()
                            result = parsed
            except httpx.HTTPStatusError as exc:
                retry = exc.response.status_code in (429, 500, 502, 503, 504)
                call.update(
                    status="error",
                    error="permission"
                    if exc.response.status_code in (401, 403)
                    else "provider_http",
                )
            except (TimeoutError, httpx.RequestError):
                retry = True
                call.update(status="error", error="timeout_or_network")
            except (ValueError, TypeError):
                call.update(status="error", error="invalid_response")
            finally:
                if result is None and call["status"] == "running":
                    call.update(status="error", error="interrupted")
                call["elapsed_ms"] = round((monotonic() - started) * 1000)
                if result is None:
                    self.store.weather_record(self.run_id, self.record)
                    self.store.event(
                        self.run_id, "tool_result", {"role": "execution", **call}
                    )
            if result is not None:
                return result
            if not retry or attempt == self.settings.weather_retries:
                break
            await asyncio.sleep(0.1 * (attempt + 1))
        reasons = {
            "permission": "认证、账户权限或额度不足",
            "provider_http": "服务请求失败",
            "timeout_or_network": "超时或网络不可用",
            "invalid_response": "服务响应无效",
        }
        self.record["gaps"].append(
            "和风查询失败："
            + reasons.get(call.get("error", ""), "查询中断")
            + "；未取得有效预报。"
        )
        return None

    def finish_call(self, status: str) -> None:
        self.record["calls"][-1]["status"] = status
        self.store.weather_record(self.run_id, self.record)
        self.store.event(
            self.run_id,
            "tool_result",
            {"role": "execution", **self.record["calls"][-1]},
        )


async def prepare_outing(
    store: Store,
    settings: Settings,
    run: dict[str, Any],
    local: datetime,
    arguments: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None,
) -> None:
    task = WeatherTask.model_validate(arguments)
    record: dict[str, Any] = {
        "task": task.model_dump(),
        "status": "running",
        "location": None,
        "date": None,
        "forecast": None,
        "candidates": [],
        "calls": [],
        "gaps": [],
        "queried_at": datetime.now(UTC).isoformat(),
        "source": "和风天气",
        "source_url": "https://www.qweather.com/",
        "attributions": [],
    }
    store.weather_record(run["id"], record)
    store.event(
        run["id"],
        "tool_call",
        {"role": "main", "tool": "prepare_outing", "input": task.model_dump()},
    )
    source = run["content"]
    source_dates = DATE_TOKEN.findall(source)
    date_text = source_dates[0] if len(source_dates) == 1 else None
    todos = [t for t in store.todos() if t["status"] == "pending"]
    todo = next(
        (t for t in todos if t["id"] == task.todo_id),
        None,
    )
    if todo and todo["id"] not in source and todo["title"] not in source:
        # Generic day plans may delegate the single scheduled outing for that day.
        try:
            plan_date = date_from_text(date_text or "今天", local.date())
        except (ValueError, Clarification):
            plan_date = None
        outings = [
            t
            for t in todos
            if t["scheduled_date"] == plan_date
            and re.search(
                r"去|前往|出门|外出|出差|旅行|旅游|参观|拜访|办事", t["title"]
            )
        ]
        if not (
            re.fullmatch(
                r"(?:请|帮我|请帮我)?(?:(?:今天|明天|后天|当天)(?:我)?"
                r"(?:该干什么|该做什么|做什么|干什么|"
                r"(?:的)?(?:出行|外出)?(?:安排|计划)(?:要|需要)?(?:准备什么)?|"
                r"(?:出行|外出)(?:要|需要)准备什么)|生成当天计划)[？?。！!\s]*",
                source,
            )
            and len(outings) == 1
            and outings[0]["id"] == todo["id"]
        ):
            todo = None
    if (
        todo
        and todo["id"] not in source
        and sum(t["title"] == todo["title"] for t in todos) > 1
    ):
        todo = None
    if (
        task.todo_id
        and todo is None
        and (not task.destination or task.destination not in source)
    ):
        record["gaps"].append("请明确本轮要查询的外出待办或目的地，不能使用无关待办。")
    if todo:
        source += " " + todo["title"]
    if not task.destination or task.destination not in source:
        record["gaps"].append(
            "请明确目的地城市及所在省/州、国家；不会根据记忆或定位猜测。"
        )
    if len(source_dates) > 1 or (task.date_text and source_dates != [task.date_text]):
        record["gaps"].append(
            "请明确一个计划日期，并重新发送包含目的地和日期的完整询问。"
        )
    elif not date_text and not (todo and todo["scheduled_date"]):
        record["gaps"].append(
            "请明确一个计划日期，并重新发送包含目的地和日期的完整询问。"
        )
    if record["gaps"]:
        record["status"] = "empty"
        finish_weather(store, run, local, record)
        return
    store.event(run["id"], "role", {"role": "execution", "status": "processing"})
    executor = QWeather(settings, store, run["id"], record, transport)
    result = await executor.request(
        "qweather_city",
        "/geo/v2/city/lookup",
        {"location": task.destination or "", "number": "20", "lang": "zh"},
    )
    if result is not None:
        try:
            locations = parse_locations(result)
        except (ValueError, KeyError, TypeError, AttributeError):
            executor.finish_call("error")
            record["status"] = "error"
            record["gaps"].append(
                "城市查询返回无效或权限不足，无法确认地点、经纬度和时区。"
            )
            finish_weather(store, run, local, record)
            return
        executor.finish_call("success" if locations else "empty")
        if len(locations) != 1 or not (
            task.destination == locations[0]["id"]
            or locations[0]["name"] in (task.destination or "")
        ):
            record["candidates"] = locations
            record["status"] = "empty"
            record["gaps"].append(
                "地点存在歧义，请选择地点ID，并重新发送包含该ID和日期的完整天气询问。"
                if locations
                else "未找到目的地，请补充城市、省/州和国家。"
            )
            finish_weather(store, run, local, record)
            return
        location = locations[0]
        record["location"] = location
        destination_today = local.astimezone(ZoneInfo(location["tz"])).date()
        try:
            record["date"] = (
                date_from_text(date_text, local.date())
                if date_text
                else todo["scheduled_date"]
                if todo
                else None
            )
        except (ValueError, Clarification):
            record["status"] = "empty"
            record["gaps"].append("请用明确的 YYYY-MM-DD 日期重新查询。")
            finish_weather(store, run, local, record)
            return
        offset = (date.fromisoformat(record["date"]) - destination_today).days
        if not 0 <= offset < settings.weather_forecast_days:
            record["status"] = "empty"
            record["gaps"].append(
                f"计划日期超出当前配置的 {settings.weather_forecast_days} 天预报范围，"
                "未查询其他日期代替。"
            )
            finish_weather(store, run, local, record)
            return
        result = await executor.request(
            "qweather_daily",
            f"/weather/v1/daily/{location['lat']}/{location['lon']}",
            {
                "days": str(settings.weather_forecast_days),
                "localTime": "true",
                "lang": "zh",
            },
        )
        if result is not None:
            try:
                record["forecast"] = parse_forecast(
                    result, record["date"], location["tz"]
                )
                attrs = result.get("metadata", {}).get("attributions", [])
                if not isinstance(attrs, list) or not all(public_url(a) for a in attrs):
                    raise ValueError("invalid attribution")
                record["attributions"] = attrs[:10]
            except (ValueError, KeyError, TypeError, AttributeError):
                record["forecast"] = None
                record["gaps"].append(
                    "天气响应无效，无法核实对应日期的数据；已确认地点保留。"
                )
                executor.finish_call("error")
            else:
                executor.finish_call("success" if record["forecast"] else "empty")
                if record["forecast"] is None:
                    record["gaps"].append("对应日期无数据，未使用其他日期的预报。")
                    record["status"] = "empty"
    if record["status"] == "running":
        record["status"] = (
            "success"
            if record["forecast"]
            else "partial"
            if record["location"]
            else "error"
        )
    finish_weather(store, run, local, record)


def number(value: Any, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("invalid number")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError("invalid number")
    return float(value)


def measurement(item: dict[str, Any], unit: str, lower: float, upper: float) -> float:
    if item["unit"] != unit:
        raise ValueError("unexpected unit")
    return number(item["value"], lower, upper)


def parse_forecast(
    result: dict[str, Any], target: str, timezone: str
) -> dict[str, Any] | None:
    days = result["days"]
    if not isinstance(days, list) or len(days) > 10:
        raise ValueError("invalid days")
    matches = []
    zone = ZoneInfo(timezone)
    for day in days:
        start = datetime.fromisoformat(day["forecastStartTime"])
        end = datetime.fromisoformat(day["forecastEndTime"])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("missing timezone")
        start, end = start.astimezone(zone), end.astimezone(zone)
        if (
            start.time().isoformat() != "00:00:00"
            or end.time().isoformat() != "00:00:00"
            or end.date() != start.date() + timedelta(days=1)
        ):
            raise ValueError("invalid daily period")
        if start.date().isoformat() == target:
            matches.append(day)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("duplicate date")
    day = matches[0]
    minimum = measurement(day["temperatureMin"], "°C", -100, 70)
    maximum = measurement(day["temperatureMax"], "°C", -100, 70)
    if minimum > maximum:
        raise ValueError("inverted temperatures")
    periods = [day["daytime"], day["nighttime"]]
    for period in periods:
        if (
            not isinstance(period["condition"]["text"], str)
            or not 0 < len(period["condition"]["text"]) <= 100
        ):
            raise ValueError("invalid condition")
    return {
        "temp_min": minimum,
        "temp_max": maximum,
        "day_text": periods[0]["condition"]["text"],
        "night_text": periods[1]["condition"]["text"],
        "precip_mm": sum(
            measurement(p["precipitation"]["amount"], "mm", 0, 3000) for p in periods
        ),
        "precip_probability": max(
            number(p["precipitation"]["probability"], 0, 1) for p in periods
        ),
        "wind_ms": max(measurement(p["wind"]["speed"], "m/s", 0, 150) for p in periods),
    }


def parse_locations(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("code") == "404":
        return []
    if result.get("code") != "200" or not isinstance(result.get("location"), list):
        raise ValueError("invalid city response")
    locations = []
    for item in result["location"][:20]:
        keys = ("id", "name", "lat", "lon", "tz", "adm1", "adm2", "country")
        if not all(
            isinstance(item.get(k), str) and 0 < len(item[k]) <= 200 for k in keys
        ):
            raise ValueError("missing city identity")
        ZoneInfo(item["tz"])
        lat, lon = float(item["lat"]), float(item["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("invalid coordinates")
        locations.append(
            {k: item[k] for k in keys} | {"lat": str(lat), "lon": str(lon)}
        )
    return locations


def finish_weather(
    store: Store, run: dict[str, Any], local: datetime, record: dict[str, Any]
) -> None:
    store.weather_record(run["id"], record)
    store.event(run["id"], "role", {"role": "execution", "status": record["status"]})
    store.event(run["id"], "role", {"role": "main", "status": "processing"})
    reply = (
        render_overview(overview(store.todos(), local.date())) + "\n\n外部天气事实：\n"
    )
    location, forecast = record["location"], record["forecast"]
    if location:
        reply += (
            f"{location['country']} {location['adm1']} {location['name']}；"
            f"经纬度 {location['lon']}, {location['lat']}；时区 {location['tz']}\n"
        )
    titles = []
    if forecast:
        reply += (
            f"适用日期：{record['date']}（目的地日期）\n"
            f"白天 {forecast['day_text']}，夜间 {forecast['night_text']}；"
            f"{forecast['temp_min']}–{forecast['temp_max']} °C；"
            f"降水 {forecast['precip_mm']} mm；风速最高 {forecast['wind_ms']} m/s。\n"
        )
        if forecast["precip_mm"] > 0:
            titles.append("准备雨具并检查出行路线")
        if forecast["temp_min"] < 15:
            titles.append("准备保暖外套")
        if forecast["temp_max"] >= 30:
            titles.append("准备饮用水并注意防暑")
        if forecast["wind_ms"] >= 10:
            titles.append("出发前检查大风影响并固定随身物品")
    else:
        reply += "未取得对应日期的有效预报，不能据此判断出行天气。\n"
    reply += (
        f"来源：和风天气 {record['source_url']}\n查询时间：{record['queried_at']}\n"
    )
    reply += "\n".join(record["gaps"])
    for candidate in record["candidates"]:
        reply += (
            f"\n{candidate['country']} {candidate['adm1']} {candidate['adm2']} "
            f"{candidate['name']} — 地点ID {candidate['id']}"
        )
    suggestions = [
        {"id": str(uuid4()), "title": title, "scheduled_date": record["date"]}
        for title in titles
    ]
    if titles:
        reply += "\n\n准备提示（行动建议，尚未加入待办）：\n" + "\n".join(titles)
    store.finish(
        run["id"],
        "completed" if record["status"] == "success" else "partial",
        reply,
        suggestions=suggestions,
        tool="prepare_outing",
    )
