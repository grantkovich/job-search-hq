import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jobhq.notify import ReminderError, allowed_endpoint, preset_due
from jobhq.worker import Queue, Schedule, Worker, daily_at, every_minutes

ET = ZoneInfo("America/New_York")
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BKey", "auth": "auth"}}


def test_presets():
    monday_10am = datetime(2026, 11, 2, 10, 0, tzinfo=ET)
    assert datetime.fromtimestamp(preset_due("tomorrow", monday_10am), ET) == datetime(2026, 11, 3, 9, 0, tzinfo=ET)
    assert datetime.fromtimestamp(preset_due("monday", monday_10am), ET) == datetime(2026, 11, 9, 9, 0, tzinfo=ET)
    assert datetime.fromtimestamp(preset_due("evening", monday_10am), ET).hour == 18
    assert preset_due("evening", monday_10am.replace(hour=19)) is None


@pytest.mark.parametrize("url, ok", [
    ("https://fcm.googleapis.com/fcm/send/x", True), ("https://web.push.apple.com/abc", True),
    ("https://wns2-par02p.notify.windows.com/w/", True), ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("http://fcm.googleapis.com/x", False), ("https://fcm.googleapis.com.evil.com/x", False), ("https://169.254.169.254/", False),
    ("https://evilnotify.windows.com.attacker.net/", False), ("https://fcm.googleapis.com:8443/x", False),
])
def test_push_endpoint_allowlist(url, ok):
    assert allowed_endpoint(url) is ok


def test_subscribe_rejects_unknown_push_service(ctx):
    with pytest.raises(ReminderError):
        ctx.push.subscribe({"endpoint": "https://attacker.example/collect", "keys": {"p256dh": "a", "auth": "b"}})


def test_reminder_fires_once_with_signed_buttons(ctx, pushes):
    ctx.push.subscribe(SUB)
    rid = ctx.reminders.set("Take the assessment", time.time() + 60)
    ctx.db.run("UPDATE reminders SET due = ? WHERE id = ?", (time.time() - 1, rid))
    assert ctx.reminders.fire_due() == 1
    assert ctx.reminders.fire_due() == 0
    _, payload = pushes[0]
    assert payload["body"] == "Take the assessment" and payload["url"] == "/reminders"
    token = payload["actions"][0]["token"]
    assert ctx.box.verify(token)["r"] == rid
    assert ctx.reminders.get(rid)["status"] == "sent"


def test_notification_buttons(client, ctx, pushes):
    rid = ctx.reminders.set("Prep", time.time() + 60)
    token = ctx.reminders.action_token(rid)
    del client.headers["X-CSRF-Token"]  # the service worker can't send it; the token proves the request instead
    assert client.post(f"/push/action/{token}/snooze").status_code == 204
    assert ctx.reminders.get(rid)["status"] == "done"
    assert len(ctx.reminders.pending()) == 1
    assert client.post("/push/action/forged.token/done").status_code == 403


def test_reminder_validation(ctx):
    with pytest.raises(ReminderError):
        ctx.reminders.set("x", time.time() - 10)
    with pytest.raises(ReminderError):
        ctx.reminders.set("x", time.time() + 400 * 86400)
    with pytest.raises(ReminderError):
        ctx.reminders.set("   ", time.time() + 600)


def test_reminder_routes(client, ctx):
    client.post("/reminders", data={"text": "Call Dana", "preset": "tomorrow"})
    rid = ctx.reminders.pending()[0]["id"]
    client.post(f"/reminders/{rid}/cancel")
    assert ctx.reminders.pending() == []
    client.post("/reminders", data={"text": "Call Dana", "preset": "", "when": (datetime.now(ET) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")})
    assert len(ctx.reminders.pending()) == 1


def test_vapid_key_is_generated_once_and_private_key_encrypted(ctx):
    key = ctx.push.public_key()
    assert key == ctx.push.public_key() and len(key) > 80
    raw = ctx.db.value("SELECT value FROM settings WHERE key = 'vapid_private_key'")
    assert "PRIVATE KEY" not in raw


def test_queue_retries_with_backoff_then_fails(ctx):
    q = Queue(ctx.db)
    jid = q.enqueue("x", {"a": 1}, max_attempts=2)
    job = q.claim()
    q.failed(job, "boom")
    row = q.get(jid)
    assert row["status"] == "queued" and row["run_at"] > time.time() + 20
    ctx.db.run("UPDATE jobs SET run_at = 0 WHERE id = ?", (jid,))
    q.failed(q.claim(), "boom again")
    assert q.get(jid)["status"] == "failed"


def test_dedupe_and_expired_leases(ctx):
    q = Queue(ctx.db)
    assert q.enqueue("x", dedupe="k") and q.enqueue("x", dedupe="k") is None
    q.claim()
    assert q.claim() is None
    ctx.db.run("UPDATE jobs SET locked_until = 0")
    assert q.claim()["attempts"] == 2  # picked up again after a crash


def test_worker_runs_handlers_and_records_failures(ctx):
    q = Queue(ctx.db)
    w = Worker(q, ET)
    done = []

    @w.on("ok")
    async def ok(p):
        done.append(p["n"])
        return {"n": p["n"]}

    @w.on("bad")
    async def bad(_p):
        raise RuntimeError("nope")

    q.enqueue("ok", {"n": 1})
    q.enqueue("bad", max_attempts=1)
    q.enqueue("unknown")
    while asyncio.run(w.run_one()):
        pass
    assert done == [1]
    statuses = {r["kind"]: r["status"] for r in ctx.db.all("SELECT kind, status FROM jobs")}
    assert statuses == {"ok": "done", "bad": "failed", "unknown": "failed"}


def test_schedules_enqueue_each_slot_once(ctx):
    q = Queue(ctx.db)
    w = Worker(q, ET, schedules=[Schedule("inbox", every_minutes(60, 7, 22)), Schedule("digest", daily_at(7, 30))])
    at = datetime(2026, 11, 3, 8, 5, tzinfo=ET)
    w.tick_schedules(at)
    w.tick_schedules(at + timedelta(minutes=10))
    kinds = [r["kind"] for r in ctx.db.all("SELECT kind FROM jobs")]
    assert kinds.count("inbox") == 1 and kinds.count("digest") == 1
    w.tick_schedules(datetime(2026, 11, 3, 23, 0, tzinfo=ET))  # quiet hours, and digest more than 3 hours late
    assert len(ctx.db.all("SELECT * FROM jobs")) == 2


def test_digest_job_pushes_a_summary(app, ctx, pushes):
    ctx.push.subscribe(SUB)
    ctx.roles.create({"company": "A", "title": "B", "next_step": "Send", "next_step_date": datetime.now(ET).date().isoformat()})
    ctx.queue.enqueue("digest")
    asyncio.run(app.state.worker.run_one())
    assert "1 next step due" in pushes[-1][1]["body"]


def test_real_web_push_encryption_and_vapid_signature(ctx, monkeypatch):
    """The real pywebpush path: what a browser's push service receives decrypts to our payload, and the VAPID JWT verifies."""
    import base64
    import json as _json

    import http_ece
    import requests
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    from jobhq.notify import Push

    def b64(data):
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    def unb64(text):
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    browser_key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/real-test",
           "keys": {"p256dh": b64(browser_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
                    "auth": b64(auth)}}
    sent = {}

    class Ok:
        status_code = 201
        text = ""
        headers: dict = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        sent.update(url=url, data=data, headers=headers)
        return Ok()

    monkeypatch.setattr(requests, "post", fake_post)
    push = Push(ctx.db, ctx.settings, ctx.box, "owner@example.com", "https://me.example.test")  # no fake sender: real pywebpush
    push.subscribe(sub)
    assert push.send("Reminder", "Take the assessment", "/reminders", tag="t") == 1
    assert sent["url"] == sub["endpoint"] and sent["headers"]["Content-Encoding"] == "aes128gcm"
    plain = http_ece.decrypt(sent["data"], private_key=browser_key, auth_secret=auth, version="aes128gcm")
    assert _json.loads(plain)["body"] == "Take the assessment"

    authz = sent["headers"]["Authorization"]
    assert authz.startswith("vapid t=")
    token = authz.split("t=", 1)[1].split(",")[0]
    key = authz.split("k=", 1)[1]
    assert key == push.public_key()
    header, claims, sig = token.split(".")
    assert _json.loads(unb64(claims))["aud"] == "https://fcm.googleapis.com" and _json.loads(unb64(claims))["sub"] == "mailto:owner@example.com"
    raw = unb64(sig)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(key))
    public.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                  f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
