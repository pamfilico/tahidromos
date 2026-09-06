"""pytest fixtures for tahidromos.

    pip install tahidromos-client

    def test_signup(inbox, page):
        page.fill("#email", inbox.address)
        page.click("#register")

        message = inbox.wait(subject_contains="Confirm")
        page.goto(message.link)

Every test gets its own mailbox, so parallel runs cannot read each other's
mail — the usual cause of a flaky email test. Inboxes created during a run
are deleted when the session ends.

Options (command line or ini):

    --tahidromos-url        default http://localhost:8080
    --tahidromos-smtp-port  default 1587
    --tahidromos-imap-port  default 1143
    --tahidromos-timeout    seconds to wait for the server, default 60
    --tahidromos-keep       keep inboxes after the run, to inspect them
"""

from __future__ import annotations

import os
import secrets

import pytest

from . import Message, Tahidromos, TahidromosError

__all__ = ["Message", "Tahidromos"]


def pytest_addoption(parser):
    group = parser.getgroup("tahidromos", "development mail server")
    group.addoption("--tahidromos-url", action="store", default=None,
                    help="Base URL of the tahidromos API [http://localhost:8080]")
    group.addoption("--tahidromos-smtp-port", action="store", type=int, default=None,
                    help="Host port for SMTP submission [1587]")
    group.addoption("--tahidromos-imap-port", action="store", type=int, default=None,
                    help="Host port for IMAP [1143]")
    group.addoption("--tahidromos-timeout", action="store", type=float, default=60.0,
                    help="Seconds to wait for the server to be ready [60]")
    group.addoption("--tahidromos-keep", action="store_true", default=False,
                    help="Do not delete inboxes at the end of the run")

    parser.addini("tahidromos_url", "Base URL of the tahidromos API")
    parser.addini("tahidromos_smtp_port", "Host port for SMTP submission")
    parser.addini("tahidromos_imap_port", "Host port for IMAP")


def _setting(config, option: str, ini: str, env: str, default):
    value = config.getoption(option, None)
    if value is not None:
        return value
    value = config.getini(ini) if ini in config._parser._inidict else None
    if value:
        return value
    return os.environ.get(env, default)


def pytest_configure(config):
    config.addinivalue_line("markers", "tahidromos: test that needs the dev mail server")


@pytest.fixture(scope="session")
def tahidromos_url(pytestconfig) -> str:
    return str(_setting(pytestconfig, "--tahidromos-url", "tahidromos_url",
                        "TAHIDROMOS_URL", "http://localhost:8080"))


@pytest.fixture(scope="session")
def mail(pytestconfig, tahidromos_url) -> Tahidromos:
    """The mail server, shared by the whole run.

    Skips the run rather than erroring if the server is not up, so a suite
    that only sometimes needs mail stays usable.
    """
    client = Tahidromos(
        url=tahidromos_url,
        smtp_port=int(_setting(pytestconfig, "--tahidromos-smtp-port",
                               "tahidromos_smtp_port", "TAHIDROMOS_SMTP_PORT", 1587)),
        imap_port=int(_setting(pytestconfig, "--tahidromos-imap-port",
                               "tahidromos_imap_port", "TAHIDROMOS_IMAP_PORT", 1143)),
        run_id=os.environ.get("TAHIDROMOS_RUN_ID") or f"pytest-{secrets.token_hex(4)}",
    )
    try:
        client.wait_until_ready(timeout=pytestconfig.getoption("--tahidromos-timeout"))
    except TahidromosError as exc:
        pytest.skip(f"tahidromos is not running at {tahidromos_url} ({exc}). "
                    f"Start it with: docker run -p 1025:25 -p 1587:587 -p 1143:143 "
                    f"-p 8080:8080 ghcr.io/pamfilico/tahidromos")

    yield client

    if not pytestconfig.getoption("--tahidromos-keep"):
        try:
            client.cleanup()
        except TahidromosError:
            pass


@pytest.fixture
def inbox(mail, request):
    """A mailbox used by this test and nothing else.

    Named after the test, so a leftover inbox tells you where it came from.
    """
    prefix = request.node.name[:32]
    box = mail.inbox(prefix=prefix)
    yield box
    if not request.config.getoption("--tahidromos-keep"):
        try:
            box.delete()
        except TahidromosError:
            pass


@pytest.fixture
def inbox_factory(mail, request):
    """Make as many isolated inboxes as the test needs.

        def test_two_people_talking(inbox_factory):
            alice, bob = inbox_factory("alice"), inbox_factory("bob")
    """
    created = []

    def make(prefix: str = "test", domain: str | None = None):
        box = mail.inbox(prefix=prefix, domain=domain)
        created.append(box)
        return box

    yield make

    if not request.config.getoption("--tahidromos-keep"):
        for box in created:
            try:
                box.delete()
            except TahidromosError:
                pass


@pytest.fixture
def echo_bot(mail):
    """The auto-responder's address, for testing a reply round-trip.

    Anything sent here comes back as a properly threaded reply, so you can
    test that your app handles an incoming reply without a second human.
    """
    bots = [box["address"]
            for app in mail.overview()["apps"]
            for box in app["mailboxes"] if box.get("is_bot")]
    if not bots:
        pytest.skip("no auto-responder mailbox is configured (BOT_ENABLED=false?)")
    return bots[0]


@pytest.fixture
def captured_inbox(mail):
    """The mailbox that catches anything addressed outside the local domains.

    Deliberately not called `captured`: that is a common name in a suite's own
    conftest, and a silent shadow is worse than a longer name.
    """
    return mail.mailbox(mail.overview()["capture_address"])
