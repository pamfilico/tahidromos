"""Auto-responders.

A bot mailbox answers anything it receives with a properly threaded reply, so
your app always has a conversation partner. Point a chat app at echo@… and you
get: send, reply, reply to the reply, for as many turns as you like.

Two bots addressed to each other volley on their own until MAX_DEPTH stops
them — the reply-to-a-reply-to-a-reply case with no human in the loop.
"""

from __future__ import annotations

import asyncio
import logging
from email.utils import parseaddr

from . import message as msg
from .config import Config
from .router import Router
from .store import Store

log = logging.getLogger("tahidromos.bot")


class AutoResponder:
    def __init__(self, store: Store, router: Router, config: Config, *,
                 poll: float = 1.0, delay: float = 0.5, max_depth: int = 8,
                 mode: str = "echo", reply_all: bool = False,
                 reply_to_bots: bool = True, quote: bool = True,
                 template: str | None = None):
        self.store = store
        self.router = router
        self.config = config
        self.poll = poll
        self.delay = delay
        self.max_depth = max_depth
        self.mode = mode
        self.reply_all = reply_all
        self.reply_to_bots = reply_to_bots
        self.quote = quote
        self.template = template or (
            "Auto-reply #{depth} from {bot}.\n\n"
            "You wrote {words} word(s). Reply again and I will keep the thread "
            "going (up to depth {max_depth})."
        )
        self.replies = 0
        self._running = False

    # -- policy ----------------------------------------------------------

    def addresses(self) -> list[str]:
        return [box.address for box in self.config.bots()]

    def compose(self, parent, bot: str, depth: int) -> str:
        text = msg.plain_body(parent)
        if self.mode == "mirror":
            return text
        if self.mode == "ack":
            return f"Received, thanks. — {bot}"
        if self.mode == "counter":
            return str(depth)
        return self.template.format(bot=bot, depth=depth, words=len(text.split()),
                                    max_depth=self.max_depth)

    def should_reply(self, parent, bot: str) -> tuple[bool, str]:
        sender = parseaddr(str(parent.get("From", "")))[1].lower()
        if not sender:
            return False, "no From address"
        if sender == bot.lower():
            return False, "message is from myself"
        auto = str(parent.get("Auto-Submitted", "")).lower()
        if auto.startswith("auto") and not self.reply_to_bots:
            return False, "auto-submitted and reply_to_bots is off"
        if str(parent.get("Precedence", "")).lower() in {"bulk", "list", "junk"}:
            return False, "bulk precedence"
        current = msg.depth(parent)
        if current >= self.max_depth:
            return False, f"depth {current} reached the limit of {self.max_depth}"
        return True, ""

    # -- work ------------------------------------------------------------

    async def handle_mailbox(self, bot: str) -> int:
        replied = 0
        for uid in self.store.unseen_uids(bot):
            raw = self.store.raw(bot, uid)
            if raw is None:
                continue
            self.store.set_flags(bot, uid, add=["\\Seen"])
            parent = msg.parse(raw)

            ok, why = self.should_reply(parent, bot)
            if not ok:
                log.debug("%s skip uid=%s (%s)", bot, uid, why)
                continue

            depth = msg.depth(parent) + 1
            reply = msg.build_reply(
                parent, self.compose(parent, bot, depth), sender=bot,
                reply_all=self.reply_all, quote=self.quote,
                domain=bot.split("@")[1],
                headers={"Auto-Submitted": "auto-replied", "X-Tahidromos-Bot": bot},
            )
            if self.delay:
                await asyncio.sleep(self.delay)
            await self.router.deliver(bot, msg.recipients(reply), msg.to_bytes(reply),
                                      submitted_by=bot, peer="autoresponder")
            self.store.set_flags(bot, uid, add=["\\Answered"])
            self.replies += 1
            replied += 1
            log.info("%s replied depth=%s to=%s subject=%r",
                     bot, depth, reply["To"], str(reply["Subject"]))
        return replied

    async def run(self) -> None:
        self._running = True
        log.info("auto-responder watching %s (mode=%s max_depth=%s)",
                 self.addresses(), self.mode, self.max_depth)
        while self._running:
            for bot in self.addresses():
                try:
                    await self.handle_mailbox(bot)
                except Exception:
                    log.exception("auto-responder failed for %s", bot)
            await asyncio.sleep(self.poll)

    def stop(self) -> None:
        self._running = False
