"""Deleting one message, archiving it, and moving it back — the per-message actions of the UI."""

from __future__ import annotations

from conftest import DOMAIN


def _deliver(api, unique, subject):
    address = f"actions-{unique}@{DOMAIN}"
    api.post("/accounts", {"address": address})
    api.post("/send", {"from": "alice", "to": address, "subject": subject, "text": "body"})
    found = api.post("/wait", {"user": address, "subject_contains": subject, "timeout": 40})
    assert found.status_code == 200, found.text
    return address, found.json()["uid"]


def test_one_message_can_be_deleted(api, unique):
    address, uid = _deliver(api, unique, f"delete me {unique}")
    gone = api.delete(f"/messages/{address}/{uid}")
    assert gone.status_code == 200 and gone.json()["deleted"] is True
    assert api.get(f"/messages/{address}").json()["count"] == 0
    assert api.get(f"/messages/{address}/{uid}").status_code == 404
    assert api.delete(f"/messages/{address}/{uid}").status_code == 404   # second time: nothing there


def test_deleting_one_leaves_the_others(api, unique):
    address, first = _deliver(api, unique, f"first {unique}")
    api.post("/send", {"from": "alice", "to": address, "subject": f"second {unique}", "text": "x"})
    api.post("/wait", {"user": address, "subject_contains": f"second {unique}", "timeout": 40})
    api.delete(f"/messages/{address}/{first}")
    left = api.get(f"/messages/{address}").json()["messages"]
    assert [m["subject"] for m in left] == [f"second {unique}"]


def test_a_bad_uid_is_rejected(api, unique):
    address, _ = _deliver(api, unique, f"bad uid {unique}")
    assert api.delete(f"/messages/{address}/abc").status_code == 422
    assert api.post(f"/messages/{address}/abc/move", {"to": "Archive"}).status_code == 422


def test_archive_moves_it_out_of_the_inbox_and_back(api, unique):
    subject = f"archive me {unique}"
    address, uid = _deliver(api, unique, subject)
    api.patch(f"/messages/{address}/{uid}", {"seen": True})

    moved = api.post(f"/messages/{address}/{uid}/move", {"to": "Archive"})
    assert moved.status_code == 200, moved.text
    archived_uid = moved.json()["uid"]
    assert moved.json()["mailbox"] == "Archive"

    assert api.get(f"/messages/{address}").json()["count"] == 0
    archive = api.get(f"/messages/{address}?mailbox=Archive").json()["messages"]
    assert [m["subject"] for m in archive] == [subject]
    assert archive[0]["seen"] is True                      # flags travel with the message
    assert "Archive" in api.get(f"/mailboxes/{address}").json()["mailboxes"]

    # it is readable — and renders — from the folder it is in
    assert api.get(f"/messages/{address}/{archived_uid}?mailbox=Archive").status_code == 200
    assert api.get(f"/messages/{address}/{archived_uid}/html?mailbox=Archive").status_code == 200

    back = api.post(f"/messages/{address}/{archived_uid}/move?mailbox=Archive", {"to": "INBOX"})
    assert back.status_code == 200
    assert [m["subject"] for m in api.get(f"/messages/{address}").json()["messages"]] == [subject]
    assert api.get(f"/messages/{address}?mailbox=Archive").json()["count"] == 0


def test_moving_a_missing_message_is_404(api, unique):
    address, _ = _deliver(api, unique, f"missing {unique}")
    assert api.post(f"/messages/{address}/99999/move", {"to": "Archive"}).status_code == 404


def test_an_archived_message_can_be_deleted_from_the_archive(api, unique):
    address, uid = _deliver(api, unique, f"archive then delete {unique}")
    archived = api.post(f"/messages/{address}/{uid}/move", {"to": "Archive"}).json()["uid"]
    assert api.delete(f"/messages/{address}/{archived}").status_code == 404          # not in INBOX
    assert api.delete(f"/messages/{address}/{archived}?mailbox=Archive").status_code == 200
    assert api.get(f"/messages/{address}?mailbox=Archive").json()["count"] == 0
