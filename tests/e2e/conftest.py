"""End-to-end tests that drive the real UI in a browser.

By default they run against whatever is already serving on TAHIDROMOS_URL.
Set TAHIDROMOS_IMAGE to have the fixtures pull and run a container instead —
which is how CI checks the *published* image, not a local build:

    TAHIDROMOS_IMAGE=ghcr.io/pamfilico/tahidromos pytest tests/e2e
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest
import requests

URL = os.environ.get("TAHIDROMOS_URL", "http://localhost:8080")
IMAGE = os.environ.get("TAHIDROMOS_IMAGE", "").strip()
CONTAINER = "tahidromos-e2e"


def _ready(url: str, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{url}/health", timeout=3).json().get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="session")
def server() -> str:
    """The base URL of a running tahidromos, started here if asked."""
    if not IMAGE:
        if not _ready(URL, timeout=20):
            pytest.skip(f"nothing serving at {URL}; start it or set TAHIDROMOS_IMAGE")
        yield URL
        return

    subprocess.run(["docker", "rm", "-f", CONTAINER],
                   capture_output=True, check=False)
    subprocess.run(["docker", "pull", IMAGE], check=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER,
         "-p", "1025:25", "-p", "1587:587", "-p", "1143:143", "-p", "8080:8080",
         IMAGE],
        check=True, capture_output=True)
    try:
        if not _ready(URL):
            logs = subprocess.run(["docker", "logs", CONTAINER],
                                  capture_output=True, text=True).stdout
            pytest.fail(f"{IMAGE} never became healthy:\n{logs[-2000:]}")
        yield URL
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER],
                       capture_output=True, check=False)


@pytest.fixture(scope="session")
def api(server):
    class Api:
        base = server

        def get(self, path, **kwargs):
            return requests.get(f"{server}{path}", timeout=30, **kwargs)

        def post(self, path, json=None, **kwargs):
            return requests.post(f"{server}{path}", json=json, timeout=60, **kwargs)

        def delete(self, path, **kwargs):
            return requests.delete(f"{server}{path}", timeout=30, **kwargs)

    return Api()


@pytest.fixture(scope="session", autouse=True)
def stack_ready(server):
    """Override the root suite's readiness check.

    That one runs before any test and assumes something is already serving.
    Here the container may not exist yet — `server` is what starts it — so
    this override makes the ordering explicit instead of racing.
    """
    return server


@pytest.fixture(scope="session", autouse=True)
def seeded(api):
    """Give the UI something to show, whatever state the server started in."""
    api.post("/templates", {"name": "receipt", "to": "alice"})
    api.post("/templates", {"name": "otp", "to": "alice"})
    api.post("/send", {"from": "alice", "to": "bob",
                       "subject": "Something to reply to", "text": "Well?"})
    api.post("/send", {"from": "noreply", "to": "alice",
                       "subject": "WIN FREE MONEY NOW!!!",
                       "text": "ACT NOW! YOU HAVE WON THE LOTTERY."})
    api.post("/send", {"from": "alice", "to": "stranger@gmail.com",
                       "subject": "Escaping", "text": "should not leave"})


@pytest.fixture
def unique() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
def ui(page, server):
    """The app, loaded and settled."""
    page.set_viewport_size({"width": 1500, "height": 950})
    page.goto(server, wait_until="networkidle")
    page.wait_for_selector('[data-testid="mailbox"]')
    return page
