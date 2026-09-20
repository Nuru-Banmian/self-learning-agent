"""IQS read-only tools with one shared per-run budget and durable attempt evidence."""

import asyncio
import ipaddress
import json
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.settings import Settings
from app.store import Store


def public_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme not in ("http", "https") or url.username or url.password:
            return False
        if (
            url.port not in (None, 80, 443)
            or "." not in host
            or host.endswith((".local", ".localhost", ".internal"))
        ):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return not host.replace(".", "").isdigit()
    except ValueError:
        return False


class IQS:
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
        self, tool: str, path: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        key = self.settings.iqs_api_key.get_secret_value()
        if not key:
            self.record["gaps"].append("IQS 未配置独立凭据，未执行搜索或正文读取。")
            return None
        for attempt in range(self.settings.search_retries + 1):
            if len(self.record["calls"]) >= self.settings.max_search_calls:
                self.record["gaps"].append("已达到搜索与正文读取的调用次数上限。")
                return None
            call: dict[str, Any] = {
                "tool": tool,
                "input": payload,
                "status": "running",
                "attempt": attempt + 1,
            }
            self.record["calls"].append(call)
            self.store.research_record(self.run_id, self.record)
            self.store.event(self.run_id, "tool_call", {"role": "execution", **call})
            started = monotonic()
            retry = False
            result = None
            try:
                async with asyncio.timeout(self.settings.search_timeout_seconds):
                    async with httpx.AsyncClient(
                        transport=self.transport,
                        trust_env=False,
                        timeout=self.settings.search_timeout_seconds,
                    ) as client:
                        async with client.stream(
                            "POST",
                            "https://cloud-iqs.aliyuncs.com" + path,
                            headers={"Authorization": "Bearer " + key},
                            json=payload,
                        ) as response:
                            call["http_status"] = response.status_code
                            response.raise_for_status()
                            content = bytearray()
                            async for chunk in response.aiter_bytes():
                                content.extend(chunk)
                                if len(content) > 512_000:
                                    raise ValueError("response too large")
                            parsed = json.loads(content)
                            if not isinstance(parsed, dict) or parsed.get("errorCode"):
                                raise ValueError("invalid response")
                            result = parsed
            except httpx.HTTPStatusError as exc:
                retry = exc.response.status_code in (429, 500, 502, 503, 504)
                call.update(status="error", error="provider_http")
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
                    self.store.research_record(self.run_id, self.record)
                    self.store.event(
                        self.run_id, "tool_result", {"role": "execution", **call}
                    )
            if result is not None:
                return result
            if not retry or attempt == self.settings.search_retries:
                break
            await asyncio.sleep(0.1 * (attempt + 1))
        self.record["gaps"].append(
            f"{tool} 未取得有效结果；服务超时、不可用或返回无效。"
        )
        return None

    async def search(self, query: str) -> None:
        result = await self.request(
            "iqs_search",
            "/search/unified",
            {
                "query": query,
                "engineType": self.settings.iqs_engine,
                "contents": {
                    "mainText": False,
                    "markdownText": False,
                    "summary": self.settings.iqs_enhanced_summary,
                },
            },
        )
        if result is None:
            return
        items = result.get("pageItems")
        if not isinstance(items, list):
            self.record["gaps"].append("搜索响应缺少有效资料列表。")
            self.finish_call("error")
            return
        for item in items[:5]:
            if (
                not isinstance(item, dict)
                or not public_url(item.get("link"))
                or not all(
                    isinstance(item.get(k), str) and item[k].strip()
                    for k in ("title", "snippet")
                )
            ):
                self.record["gaps"].append(
                    "有搜索结果缺少有效标题、链接或摘要，已忽略。"
                )
                continue
            self.record["sources"].append(
                {
                    "id": f"S{len(self.record['sources']) + 1}",
                    "title": item["title"][:500],
                    "url": item["link"],
                    "snippet": item["snippet"][:4000],
                    "material_type": "snippet",
                    "summary": item["summary"][:4000]
                    if isinstance(item.get("summary"), str)
                    else None,
                    "body": None,
                }
            )
        self.record["provider_request_id"] = result.get("requestId")
        self.finish_call(result_status(self.record))

    def finish_call(self, status: str) -> None:
        call = self.record["calls"][-1]
        call["status"] = status
        if status == "error":
            call["error"] = "invalid_result"
        self.store.research_record(self.run_id, self.record)
        self.store.event(self.run_id, "tool_result", {"role": "execution", **call})

    async def read_page(self, source: dict[str, Any]) -> None:
        result = await self.request(
            "iqs_read_page",
            "/readpage/basic",
            {
                "url": source["url"],
                "formats": ["text"],
                "maxAge": 0,
                "pageTimeout": min(
                    10000, int(self.settings.search_timeout_seconds * 500)
                ),
            },
        )
        if result is None:
            return
        data = result.get("data")
        if (
            not isinstance(data, dict)
            or data.get("statusCode") != 200
            or not isinstance(data.get("text"), str)
            or not data["text"].strip()
        ):
            self.record["gaps"].append(f"{source['id']} 正文读取失败，只保留搜索摘要。")
            self.finish_call("error")
            return
        source.update(
            body=data["text"][:10000],
            body_truncated=len(data["text"]) > 10000,
            material_type="body",
        )
        self.finish_call("success")


def result_status(record: dict[str, Any]) -> str:
    if record["gaps"]:
        return "partial" if record["sources"] else "error"
    return "success" if record["sources"] else "empty"
