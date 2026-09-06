"""The REST harness -- what an automated test suite for a chat app would drive."""

from __future__ import annotations

import uuid

from conftest import DOMAIN


def test_health(api):
    payload = api.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["domain"] == DOMAIN


def test_config_lists_local_domains(api):
    payload = api.get("/config").json()
    assert DOMAIN in payload["local_domains"]
    assert payload["primary_domain"] == DOMAIN
    assert payload["capture_address"].endswith("@" + DOMAIN)


def test_accounts_are_reachable(api):
    payload = api.get("/accounts").json()
    addresses = {a["address"] for a in payload["accounts"]}
    assert f"alice@{DOMAIN}" in addresses
    assert all(a["reachable"] for a in payload["accounts"]), payload


def test_create_account_then_use_it(api, unique):
    address = f"fresh-{unique}@{DOMAIN}"
    created = api.post("/accounts", {"address": address})
    assert created.status_code == 201, created.text

    sent = api.post("/send", {"from": "alice", "to": address,
                              "subject": f"welcome {unique}", "text": "hi there"})
    assert sent.status_code == 201, sent.text

    got = api.post("/wait", {"user": address, "message_id": sent.json()["message_id"],
                             "timeout": 40})
    assert got.status_code == 200, got.text
    assert got.json()["subject"] == f"welcome {unique}"


def test_send_auto_creates_unknown_local_recipients(api, unique):
    address = f"auto-{unique}@{DOMAIN}"
    sent = api.post("/send", {"from": "alice", "to": address,
                              "subject": f"auto create {unique}", "text": "made on demand"})
    assert sent.status_code == 201, sent.text
    assert address in sent.json()["accounts_created"]

    got = api.post("/wait", {"user": address, "subject_contains": f"auto create {unique}",
                             "timeout": 40})
    assert got.status_code == 200, got.text


def test_reply_endpoint_builds_correct_headers(api, unique):
    sent = api.post("/send", {"from": "alice", "to": "bob",
                              "subject": f"api reply {unique}", "text": "question"})
    original_id = sent.json()["message_id"]

    delivered = api.post("/wait", {"user": "bob", "message_id": original_id, "timeout": 40})
    assert delivered.status_code == 200, delivered.text

    replied = api.post("/reply", {"user": "bob", "uid": delivered.json()["uid"],
                                  "text": "answer"})
    assert replied.status_code == 201, replied.text
    body = replied.json()
    assert body["in_reply_to"] == original_id
    assert body["references"] == [original_id]
    assert body["subject"] == f"Re: api reply {unique}"

    back = api.post("/wait", {"user": "alice", "message_id": body["message_id"], "timeout": 40})
    assert back.status_code == 200, back.text
    assert back.json()["in_reply_to"] == original_id


def test_reply_by_message_id(api, unique):
    sent = api.post("/send", {"from": "alice", "to": "bob",
                              "subject": f"by message id {unique}", "text": "q"})
    original_id = sent.json()["message_id"]
    api.post("/wait", {"user": "bob", "message_id": original_id, "timeout": 40})

    replied = api.post("/reply", {"user": "bob", "message_id": original_id, "text": "a"})
    assert replied.status_code == 201, replied.text
    assert replied.json()["in_reply_to"] == original_id


def test_conversation_endpoint_builds_a_real_thread(api, unique):
    result = api.post("/conversation", {
        "participants": ["alice", "bob"],
        "subject": f"scripted thread {unique}",
        "turns": 6,
    })
    assert result.status_code == 201, result.text
    payload = result.json()
    assert payload["turns"] == 6

    last = payload["messages"][-1]
    assert last["references"][0] == payload["thread_root"]
    assert len(last["references"]) == 5
    assert last["depth"] == 5


def test_threads_endpoint_groups_by_root(api, unique):
    subject = f"grouped {unique}"
    api.post("/conversation", {"participants": ["alice", "bob"],
                               "subject": subject, "turns": 4})

    threads = api.get("/threads/bob").json()["threads"]
    match = [t for t in threads if subject in t["subject"]]
    assert match, f"no thread for {subject}"
    assert match[0]["message_count"] >= 2
    assert f"alice@{DOMAIN}" in match[0]["participants"]


def test_wait_times_out_with_408(api):
    response = api.post("/wait", {"user": "alice", "subject_contains": uuid.uuid4().hex,
                                  "timeout": 3})
    assert response.status_code == 408


def test_purge_clears_the_mailbox(api, unique):
    address = f"purge-{unique}@{DOMAIN}"
    api.post("/accounts", {"address": address})
    api.post("/send", {"from": "alice", "to": address, "subject": f"tmp {unique}", "text": "x"})
    api.post("/wait", {"user": address, "subject_contains": f"tmp {unique}", "timeout": 40})

    purged = api.delete(f"/messages/{address}")
    assert purged.status_code == 200
    assert purged.json()["deleted"] >= 1
    assert api.get(f"/messages/{address}").json()["count"] == 0


def test_raw_message_is_a_real_rfc5322_document(api, unique):
    sent = api.post("/send", {"from": "alice", "to": "bob",
                              "subject": f"raw {unique}", "text": "raw body"})
    delivered = api.post("/wait", {"user": "bob", "message_id": sent.json()["message_id"],
                                   "timeout": 40}).json()

    raw = api.get(f"/messages/bob/{delivered['uid']}/raw").text
    assert "Message-ID:" in raw
    assert f"Subject: raw {unique}" in raw
    assert "raw body" in raw
