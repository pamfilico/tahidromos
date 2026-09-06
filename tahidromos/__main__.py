"""One process, every service.

    python -m tahidromos

Starts the SMTP server, the submission server, the IMAP server, the HTTP API
and UI, and the auto-responders, all on one asyncio loop. There is nothing
else to run and nothing else to install.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import uvicorn

from . import config as config_module
from .api import create_app
from .bot import AutoResponder
from .imap import ImapServer
from .router import Router
from .smtp import SmtpServer
from .store import Store
from .tls import build_context

log = logging.getLogger("tahidromos")

BANNER = r"""
   ____        __    _     __
  / __/__ ____/ /   (_)___/ /______  __ _  ___  ___
 / _// _ `/ _  /   / / __/ __/ __/ |/ /  |/ _ \(_-<
/_/  \_,_/\_,_/   /_/_/  \__/_/  |___/|_/\___/___/     tahidromos
"""


def flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def number(name: str, default: str) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


async def serve() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-5s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    started_at = time.time()
    data_dir = Path(os.environ.get("TAHIDROMOS_DATA", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    config = config_module.load()
    store = Store(str(data_dir / "tahidromos.db"))

    # ------------------------------------------------------------ seed
    for app_config in config.apps:
        if app_config.smtp_address:
            store.create_account(app_config.smtp_address, app_config.smtp_password,
                                 app=app_config.name,
                                 description=f"SMTP credentials for {app_config.name}")
        for mailbox in app_config.mailboxes:
            store.create_account(mailbox.address, mailbox.password, app=app_config.name,
                                 is_bot=mailbox.is_bot, description=mailbox.description)

    router = Router(store, config, auto_create=flag("MAIL_AUTO_CREATE", "true"))

    host = os.environ.get("BIND_HOST", "0.0.0.0")
    tls_context = None
    if flag("ENABLE_TLS", "true"):
        tls_context = build_context(data_dir / "tls", config.hostname)

    smtp = SmtpServer(store, router, config.hostname, require_auth=False,
                      tls_context=tls_context)
    submission = SmtpServer(store, router, config.hostname, require_auth=True,
                            tls_context=tls_context)
    imap = ImapServer(store, tls_context=tls_context)

    servers = [
        await smtp.listen(host, number("SMTP_PORT", "25")),
        await submission.listen(host, number("SUBMISSION_PORT", "587")),
        await imap.listen(host, number("IMAP_PORT", "143")),
    ]
    if tls_context is not None:
        servers.append(await submission.listen(host, number("SUBMISSION_TLS_PORT", "465"),
                                               tls=True))
        servers.append(await imap.listen(host, number("IMAPS_PORT", "993"), tls=True))

    # ------------------------------------------------------------ bots
    responder = AutoResponder(
        store, router, config,
        poll=float(os.environ.get("BOT_POLL", "1")),
        delay=float(os.environ.get("BOT_DELAY", "0.5")),
        max_depth=number("BOT_MAX_DEPTH", "8"),
        mode=os.environ.get("BOT_MODE", "echo"),
        reply_all=flag("BOT_REPLY_ALL", "false"),
        reply_to_bots=flag("BOT_REPLY_TO_BOTS", "true"),
        quote=flag("BOT_QUOTE", "true"),
        template=os.environ.get("BOT_TEMPLATE") or None,
    )
    tasks = []
    if flag("BOT_ENABLED", "true") and responder.addresses():
        tasks.append(asyncio.create_task(responder.run()))

    # ------------------------------------------------------------ http
    http_port = number("HTTP_PORT", "8080")
    api = create_app(store, router, config, started_at)
    http = uvicorn.Server(uvicorn.Config(api, host=host, port=http_port,
                                         log_level="warning", access_log=False))
    tasks.append(asyncio.create_task(http.serve()))

    print(BANNER, file=sys.stderr)
    log.info("domains      %s", " ".join(config.domains))
    log.info("apps         %s", ", ".join(f"{a.name} (@{a.domain})" for a in config.apps))
    log.info("mailboxes    %d", len(store.accounts()))
    log.info("auto-reply   %s", ", ".join(responder.addresses()) or "(none)")
    log.info("capture      %s", config.capture_address)
    log.info("http         http://localhost:%s", http_port)
    if config.sources:
        log.info("config       %s", ", ".join(config.sources))

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, signal_name), stopping.set)
        except (NotImplementedError, AttributeError):
            pass

    await stopping.wait()
    log.info("shutting down")
    responder.stop()
    http.should_exit = True
    for server in servers:
        server.close()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def main() -> int:
    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
