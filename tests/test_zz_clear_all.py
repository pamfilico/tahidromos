"""Clear all — DELETE /messages empties every mailbox of every account.

Named to run LAST in the root suite: it wipes the whole server, which every other test shares.
(The browser suite seeds its own mail and runs separately.)
"""

from __future__ import annotations

from conftest import DOMAIN


def test_clear_all_empties_every_mailbox_but_keeps_the_accounts(api, unique):
    first = f"clear-a-{unique}@{DOMAIN}"
    second = f"clear-b-{unique}@{DOMAIN}"
    for address in (first, second):
        api.post("/accounts", {"address": address})
        api.post("/send", {"from": "alice", "to": address, "subject": f"clear {unique}", "text": "x"})
        api.post("/wait", {"user": address, "subject_contains": f"clear {unique}", "timeout": 40})
    archived_uid = api.get(f"/messages/{second}").json()["messages"][0]["uid"]
    api.post(f"/messages/{second}/{archived_uid}/move", {"to": "Archive"})

    cleared = api.delete("/messages")
    assert cleared.status_code == 200 and cleared.json()["deleted"] >= 2

    assert api.get("/overview").json()["total"] == 0
    for address in (first, second):
        assert api.get(f"/messages/{address}").json()["count"] == 0
    assert api.get(f"/messages/{second}?mailbox=Archive").json()["count"] == 0
    accounts = {a["address"] for a in api.get("/accounts").json()["accounts"]}
    assert {first, second} <= accounts                       # mailboxes survive, only mail goes

    # and the server keeps working afterwards
    api.post("/send", {"from": "alice", "to": first, "subject": f"after {unique}", "text": "x"})
    assert api.post("/wait", {"user": first, "subject_contains": f"after {unique}",
                              "timeout": 40}).status_code == 200
