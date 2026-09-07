"""Driving the real interface in a real browser.

Everything here is located by data-testid, so a restyle does not break the
suite and a rename shows up as a failure rather than a silent no-op.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect


def select_mailbox(page, address: str):
    """Click a mailbox and wait for its list to finish loading.

    The header updates immediately but the messages arrive over HTTP, so
    without waiting for the list to settle a count() can run against a
    spinner and see nothing.
    """
    page.click(f'[data-testid="mailbox"][data-address="{address}"]')
    expect(page.locator('[data-testid="list-subtitle"]')).to_have_text(address)
    page.wait_for_function(
        """() => {
            const list = document.querySelector('[data-testid="message-list"]');
            if (!list) return false;
            return list.querySelector('[data-testid="message-row"]')
                || list.querySelector('[data-testid="empty-mailbox"]');
        }""",
        timeout=15_000,
    )


def open_message(page, contains: str):
    row = page.locator('[data-testid="message-row"]', has_text=contains).first
    row.click()
    expect(page.locator('[data-testid="message-headline"]')).to_be_visible()
    return row


# ---------------------------------------------------------------- shell


def test_the_app_loads_with_its_identity(ui):
    expect(ui).to_have_title("tahidromos")
    logo = ui.locator('[data-testid="brand-logo"]')
    expect(logo).to_be_visible()
    assert ui.evaluate(
        '() => { const i = document.querySelector(\'[data-testid="brand-logo"]\');'
        ' return i.complete && i.naturalWidth > 0; }'), "the logo did not load"

    for panel in ("sidebar", "message-pane", "reader"):
        expect(ui.locator(f'[data-testid="{panel}"]')).to_be_visible()


def test_the_counters_agree_with_the_api(ui, api):
    overview = api.get("/overview").json()
    expect(ui.locator('[data-testid="stat-unread-value"]')).to_have_text(str(overview["unread"]))
    expect(ui.locator('[data-testid="stat-total-value"]')).to_have_text(str(overview["total"]))

    mailboxes = sum(len(app["mailboxes"]) for app in overview["apps"])
    expect(ui.locator('[data-testid="stat-mailboxes-value"]')).to_have_text(str(mailboxes))
    expect(ui.locator('[data-testid="mailbox"]')).to_have_count(mailboxes)


def test_apps_group_their_mailboxes(ui, api):
    for app in api.get("/overview").json()["apps"]:
        group = ui.locator(f'[data-testid="app-group"][data-app="{app["name"]}"]')
        expect(group).to_have_count(1)
        expect(group.locator('[data-testid="app-domain"]')).to_have_text(f'@{app["domain"]}')
        expect(group.locator('[data-testid="mailbox"]')).to_have_count(len(app["mailboxes"]))


def test_an_app_group_collapses_and_remembers(ui):
    group = ui.locator('[data-testid="app-group"]').first
    header = group.locator('[data-testid="app-header"]')

    header.click()
    expect(group).to_have_class(re.compile(r"collapsed"))

    ui.reload(wait_until="networkidle")
    reopened = ui.locator('[data-testid="app-group"]').first
    expect(reopened).to_have_class(re.compile(r"collapsed"))

    reopened.locator('[data-testid="app-header"]').click()
    expect(reopened).not_to_have_class(re.compile(r"collapsed"))


def test_the_theme_toggles_and_persists(ui):
    start = ui.evaluate("() => document.documentElement.dataset.theme")
    ui.click('[data-testid="theme-toggle"]')
    flipped = ui.evaluate("() => document.documentElement.dataset.theme")
    assert flipped != start

    ui.reload(wait_until="networkidle")
    assert ui.evaluate("() => document.documentElement.dataset.theme") == flipped

    ui.click('[data-testid="theme-toggle"]')
    assert ui.evaluate("() => document.documentElement.dataset.theme") == start


# ---------------------------------------------------------------- reading


def test_opening_a_message_shows_its_headers(ui, api):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")

    expect(ui.locator('[data-testid="meta-from"]')).to_contain_text("@")
    expect(ui.locator('[data-testid="meta-to"]')).to_contain_text("alice@tahidromos.test")
    expect(ui.locator('[data-testid="meta-message-id"]')).to_contain_text("@")
    expect(ui.locator('[data-testid="meta-date"]')).not_to_be_empty()


def test_reading_does_not_mark_a_message_read(ui, api):
    """Browsing must not perturb a test waiting on an unread count."""
    before = api.get("/overview").json()["unread"]
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.wait_for_timeout(600)
    assert api.get("/overview").json()["unread"] == before


def test_marking_read_moves_the_badge(ui, api, unique):
    api.post("/send", {"from": "alice", "to": "dave",
                       "subject": f"badge {unique}", "text": "x"})
    ui.reload(wait_until="networkidle")
    select_mailbox(ui, "dave@tahidromos.test")

    badge = ui.locator('[data-testid="mailbox"][data-address="dave@tahidromos.test"]'
                       ' [data-testid="mailbox-badge"]')
    before = int(badge.get_attribute("data-unread"))

    open_message(ui, f"badge {unique}")
    ui.click('[data-testid="seen-toggle"]')
    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Marked read")
    expect(badge).to_have_attribute("data-unread", str(before - 1))


def test_the_body_can_be_read_three_ways(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")

    expect(ui.locator('[data-testid="view-text"]')).to_have_class(re.compile(r"\bon\b"))
    expect(ui.locator('[data-testid="message-body"]')).to_contain_text("Thanks")

    ui.click('[data-testid="view-raw"]')
    expect(ui.locator('[data-testid="message-body"]')).to_contain_text("Message-ID:")

    ui.click('[data-testid="view-html"]')
    expect(ui.locator('[data-testid="preview-frame"]')).to_be_visible()


def test_threads_group_and_ungroup(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    expect(ui.locator('[data-testid="thread-group"]')).to_have_count(0)

    ui.click('[data-testid="threads-toggle"]')
    assert ui.locator('[data-testid="thread-group"]').count() > 0
    expect(ui.locator('[data-testid="thread-header"]').first).to_contain_text("message")

    ui.click('[data-testid="threads-toggle"]')
    expect(ui.locator('[data-testid="thread-group"]')).to_have_count(0)


# ---------------------------------------------------------------- writing


def test_composing_delivers_to_the_recipient(ui, api, unique):
    select_mailbox(ui, "alice@tahidromos.test")
    ui.click('[data-testid="compose-button"]')

    expect(ui.locator('[data-testid="composer-title"]')).to_have_text("New message")
    expect(ui.locator('[data-testid="composer-from"]')).to_have_value("alice@tahidromos.test")

    ui.fill('[data-testid="composer-to"]', "bob@tahidromos.test")
    ui.fill('[data-testid="composer-subject"]', f"From the browser {unique}")
    ui.fill('[data-testid="composer-body"]', "Typed into the real interface.")
    ui.click('[data-testid="composer-send"]')

    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Sent")
    expect(ui.locator('[data-testid="composer-sheet"]')).not_to_have_class(re.compile(r"open"))

    arrived = api.post("/wait", {"user": "bob@tahidromos.test",
                                 "subject_contains": unique, "timeout": 20})
    assert arrived.status_code == 200
    assert "Typed into the real interface." in arrived.json()["text"]


def test_composing_to_an_unknown_address_creates_the_mailbox(ui, api, unique):
    """The address does not have to exist first."""
    address = f"ghost-{unique}@tahidromos.test"
    select_mailbox(ui, "alice@tahidromos.test")

    ui.click('[data-testid="compose-button"]')
    ui.fill('[data-testid="composer-to"]', address)
    ui.fill('[data-testid="composer-subject"]', f"Conjured {unique}")
    ui.fill('[data-testid="composer-body"]', "You did not exist a moment ago.")
    ui.click('[data-testid="composer-send"]')
    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Sent")

    assert api.post("/wait", {"user": address, "subject_contains": unique,
                              "timeout": 20}).status_code == 200

    # and it must show up in the sidebar, tagged
    ui.reload(wait_until="networkidle")
    row = ui.locator(f'[data-testid="mailbox"][data-address="{address}"]')
    expect(row).to_have_count(1)
    expect(row.locator('[data-testid="tag-new"]')).to_be_visible()


def test_replying_from_the_browser_threads(ui, api, unique):
    api.post("/send", {"from": "bob", "to": "alice",
                       "subject": f"Please reply {unique}", "text": "Well?"})
    ui.reload(wait_until="networkidle")
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, f"Please reply {unique}")

    ui.click('[data-testid="reply-button"]')
    expect(ui.locator('[data-testid="composer-title"]')).to_have_text("Reply")
    expect(ui.locator('[data-testid="composer-to"]')).to_be_disabled()
    expect(ui.locator('[data-testid="composer-thread-note"]')).to_contain_text("threaded onto")

    ui.fill('[data-testid="composer-body"]', "Replied from the browser.")
    ui.click('[data-testid="composer-send"]')
    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Replied")

    back = api.post("/wait", {"user": "bob@tahidromos.test",
                              "subject_contains": unique, "unseen_only": True,
                              "timeout": 20}).json()
    assert back["subject"] == f"Re: Please reply {unique}"
    assert back["in_reply_to"] is not None


def test_forwarding_from_the_browser_carries_attachments(ui, api, unique):
    sent = api.post("/scenarios", {"name": "attachment", "to": "alice", "seed": 5}).json()
    api.post("/wait", {"user": "alice@tahidromos.test",
                       "message_id": sent["message_id"], "timeout": 20})

    ui.reload(wait_until="networkidle")
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "Invoice")

    ui.click('[data-testid="forward-button"]')
    expect(ui.locator('[data-testid="composer-title"]')).to_have_text("Forward")
    expect(ui.locator('[data-testid="composer-subject"]')).to_have_value(re.compile(r"^Fwd: "))
    expect(ui.locator('[data-testid="composer-thread-note"]')).to_contain_text("attachment")

    ui.fill('[data-testid="composer-to"]', "carol@tahidromos.test")
    ui.fill('[data-testid="composer-body"]', f"Forwarded from the browser {unique}")
    ui.click('[data-testid="composer-send"]')
    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Forwarded")

    arrived = api.post("/wait", {"user": "carol@tahidromos.test",
                                 "subject_contains": "Invoice", "unseen_only": True,
                                 "timeout": 20}).json()
    assert arrived["subject"].startswith("Fwd:")
    assert arrived["in_reply_to"] is None, "a forward is not a reply"
    names = {a["filename"] for a in arrived["attachments"]}
    assert "invoice-10432.pdf" in names, f"attachments lost: {names}"


def test_escape_closes_the_composer(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    ui.click('[data-testid="compose-button"]')
    expect(ui.locator('[data-testid="composer-sheet"]')).to_have_class(re.compile(r"open"))
    ui.keyboard.press("Escape")
    expect(ui.locator('[data-testid="composer-sheet"]')).not_to_have_class(re.compile(r"open"))


# ---------------------------------------------------------------- preview


def test_the_device_preview_resizes_the_frame(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.click('[data-testid="view-html"]')
    expect(ui.locator('[data-testid="preview-frame"]')).to_be_visible()

    for device, width, height in [("iPhone SE", 375, 667),
                                  ("iPad mini", 768, 1024),
                                  ("Outlook (600)", 600, 900)]:
        ui.select_option('[data-testid="device-select"]', device)
        expect(ui.locator('[data-testid="device-dims"]')).to_have_text(f"{width} × {height}")
        frame = ui.locator('[data-testid="device-frame"]')
        assert abs(frame.evaluate("el => el.getBoundingClientRect().width / "
                                  "(el.style.transform.match(/[\\d.]+/)?.[0] ?? 1)")
                   - width) < 3, f"{device} frame is not {width}px"


def test_the_template_is_readable_at_phone_width(ui):
    """A template that overflows at 393px is broken, however good it looks wide."""
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.click('[data-testid="view-html"]')
    ui.select_option('[data-testid="device-select"]', "iPhone 15")
    ui.wait_for_timeout(700)

    overflow = ui.evaluate("""() => {
        const f = document.querySelector('[data-testid="preview-frame"]');
        const d = f.contentDocument;
        return { scroll: d.documentElement.scrollWidth, frame: f.clientWidth };
    }""")
    assert overflow["scroll"] <= overflow["frame"] + 2, \
        f"content is {overflow['scroll']}px in a {overflow['frame']}px frame"


def test_the_email_carries_its_own_logo(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.click('[data-testid="view-html"]')
    ui.wait_for_timeout(700)

    logo = ui.evaluate("""() => {
        const d = document.querySelector('[data-testid="preview-frame"]').contentDocument;
        const img = d.querySelector('img[src^="data:image/png"]');
        return img ? { loaded: img.complete && img.naturalWidth > 0 } : null;
    }""")
    assert logo and logo["loaded"], "the inlined mark is missing or did not decode"


def test_the_code_is_legible_in_the_one_time_password_template(ui):
    """A white code on a white card is the bug this guards."""
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "sign-in code")
    ui.click('[data-testid="view-html"]')
    ui.wait_for_timeout(700)

    contrast = ui.evaluate("""() => {
        const d = document.querySelector('[data-testid="preview-frame"]').contentDocument;
        const cell = [...d.querySelectorAll('td')]
            .find(td => !td.children.length && /^\\d{6}$/.test(td.textContent.trim()));
        if (!cell) return null;
        const rgb = s => (s.match(/\\d+/g) || []).slice(0, 3).map(Number);
        const lum = ([r, g, b]) => 0.2126*r + 0.7152*g + 0.0722*b;
        return { text: lum(rgb(getComputedStyle(cell).color)),
                 background: lum(rgb(getComputedStyle(d.body).backgroundColor)) };
    }""")
    assert contrast, "no six-digit code cell found"
    assert abs(contrast["text"] - contrast["background"]) > 100, \
        f"code and background are too close: {contrast}"


def test_zoom_controls_work(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.click('[data-testid="view-html"]')
    ui.wait_for_timeout(500)

    ui.click('[data-testid="zoom-in"]')
    after = ui.locator('[data-testid="zoom-level"]').inner_text()
    ui.click('[data-testid="zoom-out"]')
    expect(ui.locator('[data-testid="zoom-level"]')).not_to_have_text(after)

    ui.click('[data-testid="zoom-fit"]')
    expect(ui.locator('[data-testid="zoom-level"]')).to_contain_text("%")


def test_saving_a_png_downloads_a_real_image(ui, tmp_path):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    ui.click('[data-testid="view-html"]')
    ui.wait_for_timeout(900)

    with ui.expect_download(timeout=30_000) as download:
        ui.click('[data-testid="save-png"]')
    saved = tmp_path / "preview.png"
    download.value.save_as(saved)

    assert saved.stat().st_size > 5_000, "the PNG is suspiciously small"
    assert saved.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


def test_downloading_the_eml_gives_a_real_message(ui, tmp_path):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")

    with ui.expect_download(timeout=30_000) as download:
        ui.click('[data-testid="download-eml"]')
    saved = tmp_path / "message.eml"
    download.value.save_as(saved)

    body = saved.read_text(errors="replace")
    assert "Message-ID:" in body and "Subject:" in body


# ---------------------------------------------------------------- spam


def test_the_spam_badge_explains_a_flagged_message(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "WIN FREE MONEY")

    badge = ui.locator('[data-testid="spam-badge"]')
    expect(badge).to_be_visible()
    assert badge.get_attribute("data-verdict") in ("suspicious", "spam")

    ui.click('[data-testid="spam-toggle"]')
    expect(ui.locator('[data-testid="spam-rules"]')).to_be_visible()
    assert ui.locator('[data-testid="spam-rule"]').count() > 0


def test_a_template_is_not_flagged(ui):
    select_mailbox(ui, "alice@tahidromos.test")
    open_message(ui, "receipt")
    badge = ui.locator('[data-testid="spam-badge"]')
    expect(badge).to_be_visible()
    assert badge.get_attribute("data-verdict") == "ham", \
        "a template we ship should never be flagged"


# ---------------------------------------------------------------- capture


def test_the_capture_mailbox_holds_what_would_have_escaped(ui, api):
    capture = api.get("/overview").json()["capture_address"]
    select_mailbox(ui, capture)

    row = ui.locator(f'[data-testid="mailbox"][data-address="{capture}"]')
    expect(row.locator('[data-testid="tag-capture"]')).to_be_visible()
    assert ui.locator('[data-testid="message-row"]').count() > 0


def test_emptying_a_mailbox_clears_it(ui, api, unique):
    address = f"disposable-{unique}@tahidromos.test"
    api.post("/send", {"from": "alice", "to": address,
                       "subject": f"temp {unique}", "text": "x"})
    api.post("/wait", {"user": address, "subject_contains": unique, "timeout": 20})

    ui.reload(wait_until="networkidle")
    select_mailbox(ui, address)
    assert ui.locator('[data-testid="message-row"]').count() >= 1

    ui.click('[data-testid="empty-button"]')
    expect(ui.locator('[data-testid="toast"]')).to_contain_text("Deleted")
    expect(ui.locator('[data-testid="empty-mailbox"]')).to_be_visible()
