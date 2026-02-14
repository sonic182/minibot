from __future__ import annotations

from collections.abc import AsyncGenerator
import contextlib
import socket
from threading import Thread
import time
from typing import Any

import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def fake_llm_server() -> AsyncGenerator[dict[str, Any], None]:
    pytest.importorskip("starlette")
    uvicorn = pytest.importorskip("uvicorn")
    from tests.fixtures.llm.fake_openai_api import FakeLLMState, build_app

    state = FakeLLMState()
    app = build_app(state)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config=config)

    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)

    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}/v1",
            "state": state,
        }
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            thread.join(timeout=3)
