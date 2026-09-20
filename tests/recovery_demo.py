"""Isolated recovery browser fixture; DB_PATH must point to a disposable database.

All providers are simulated. A gate file holds requests until explicitly released:
set RECOVERY_GATE to its path, create the file to release, remove it to hold again.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx

from app.main import create_app
from app.settings import Settings
from tests.test_chat import tool_response


def create_demo():
    database = Path(os.environ["DB_PATH"])
    gate = Path(os.environ["RECOVERY_GATE"])

    async def provider(request):
        while not gate.exists():
            await asyncio.sleep(0.1)
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        title = "整理书架" if "书架" in content else "整理书桌"
        return httpx.Response(
            200, json=tool_response([{"title": title, "date_text": None}])
        )

    return create_app(
        Settings(
            _env_file=None,
            db_path=database,
            dashscope_api_key="browser-fixture",
            run_timeout_seconds=120,
        ),
        transport=httpx.MockTransport(provider),
    )
