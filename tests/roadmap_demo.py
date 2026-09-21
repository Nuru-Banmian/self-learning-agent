"""Deterministic browser/process fixture. Only external providers are replaced."""

import asyncio
import json
import os
from pathlib import Path

import httpx

from app.main import create_app
from app.settings import Settings
from tests.test_roadmap_batch import batch_provider
from tests.test_roadmaps import roadmap_provider


def create_demo_app():
    base = (
        batch_provider if os.environ.get("ROADMAP_BATCH_DEMO") else roadmap_provider([])
    )

    async def provider(request):
        body = json.loads(request.content)
        gate = os.environ.get("ROADMAP_PROVIDER_GATE")
        if (
            gate
            and body.get("tools", [{}])[0].get("function", {}).get("name")
            == "roadmap_answer"
        ):
            while not Path(gate).exists():
                await asyncio.sleep(0.02)
        return base(request)

    return create_app(
        Settings(
            _env_file=None,
            db_path=Path(os.environ["DB_PATH"]),
            dashscope_api_key="fixture",
            iqs_api_key="fixture",
        ),
        transport=httpx.MockTransport(provider),
    )
