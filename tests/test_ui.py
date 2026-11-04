"""Page markup that keyboard, screen-reader, phone and installed-app users depend on."""
import json
import re
import struct

from jobhq.app import STATIC


def trigger(response) -> dict:
    return json.loads(response.headers.get("hx-trigger") or "{}")


def test_skip_link_main_landmark_and_install_metadata(client):
    page = client.get("/").text
    assert '<a class="skip" href="#main">Skip to content</a>' in page and 'id="main"' in page
    assert 'name="apple-mobile-web-app-title" content="Job Search HQ"' in page and 'name="apple-mobile-web-app-capable"' in page
    assert 'href="/static/apple-touch-icon.png"' in page
    assert 'media="(prefers-color-scheme: dark)"' in page  # a dark theme-color too
    for label in ("Inbox", "Reminders", "Suggestions", "Setup", "Settings"):
        assert f">{label}" in page or f">\n      {label}" in page


def test_titles_name_the_role_document_and_question(client, ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops Lead"})
    assert "<title>Ops Lead at Acme · Job Search HQ</title>" in client.get(f"/roles/{rid}").text
    did = ctx.db.run("INSERT INTO documents (role_id, kind, status, content, checks, created, updated) VALUES (?, 'cover_letter', 'draft', ?, '[]', 0, 0)",
                     (rid, json.dumps({"paragraphs": [], "greeting": "", "closing": ""}))).lastrowid
    assert "<title>Cover letter, Ops Lead at Acme · Job Search HQ</title>" in client.get(f"/documents/{did}").text
    from jobhq import practice
    qid = practice.new_question(ctx, rid, "Tell me about a time you calmed down an upset customer at the counter.", "service")
    title = re.search(r"<title>(.*?)</title>", client.get(f"/practice/q/{qid}").text).group(1)
    assert title.startswith("Practice: Tell me about a time") and title.endswith("· Job Search HQ")


def test_reloads_carry_a_message(client, ctx):
    exp = ctx.db.run("INSERT INTO experience (company, title) VALUES ('Acme', 'Cashier')").lastrowid
    r = client.post("/profile/facts", data={"text": "Trained 6 new hires", "experience_id": str(exp)})
    assert r.headers["hx-refresh"] == "true" and trigger(r)["jobhq:toast"] == "Fact added."
    r = client.post("/reminders", data={"text": "Call back", "preset": "tomorrow"})
    assert r.headers["hx-refresh"] == "true" and trigger(r)["jobhq:toast"].startswith("Reminder set")


def test_profile_errors_name_their_fields(client):
    r = client.post("/profile/basics", data={"name": "Pat", "email": "not-an-email"})
    assert r.status_code == 400
    t = trigger(r)
    assert "jobhq:error" in t and "email" in t["jobhq:fields"]["fields"]


def test_job_poll_says_nothing_while_working(client, ctx):
    job_id = ctx.queue.enqueue("score", {"role_id": 1})
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 204 and not r.content


def test_practice_poll_only_refreshes_the_attempt(client, ctx):
    from jobhq import practice
    qid = practice.new_question(ctx, None, "Why this job?", "motivation")
    practice.add_attempt(ctx, qid, "Because I like helping people and this team does that every day.", "typed", 30)
    page = client.get(f"/practice/q/{qid}").text
    assert 'hx-select=".main"' not in page
    assert re.search(r'id="attempt-body-(\d+)"[^>]*hx-select="#attempt-body-\1"[^>]*hx-target="this"', page)


def test_controls_have_names(client, ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    page = client.get(f"/roles/{rid}").text
    for name in ("What to remind you about", "When", "Kind of entry", "Date", "What happened"):
        assert f'aria-label="{name}"' in page
    assert 'aria-pressed="true"' in page and "disabled>" not in page
    assert 'id="work" class="small" role="status"' in page
    chat = client.get("/chat").text
    assert 'role="log"' in chat and 'aria-label="Conversation"' in chat and "Shift+Enter" in chat


def test_education_without_degree_has_no_stray_comma(client, ctx):
    ctx.db.run("INSERT INTO education (school, degree, field, start_date, end_date) VALUES ('Lincoln High School', '', '', '', '2019')")
    page = client.get("/profile").text
    assert "<li>Lincoln High School" in page and ", Lincoln High School" not in page
    assert 'step="any"' in page


def test_examples_are_not_only_for_marketers(client, ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    pages = client.get("/profile").text + client.get(f"/roles/{rid}").text + client.get("/setup").text
    for old in ("Marketing Operations Leader", "Migrated 75,000", "e.g. MBA", "Director of Marketing Operations", '"Marketing Operations Manager"'):
        assert old not in pages


def test_setup_keeps_admin_steps_apart(client):
    page = client.get("/setup").text
    admin = page.index("For whoever runs your server")
    for jargon in ("sudo jobsctl", "sslip.io", "docs/cheap.md", "Part 3"):
        assert page.index(jargon) > admin, jargon


def test_settings_ai_table_scrolls_inside_its_card(client):
    page = client.get("/settings").text
    assert '<div class="table-wrap"><table class="table small cards">' in page


def test_manifest_icons_and_offline_page(anon):
    m = anon.get("/manifest.webmanifest").json()
    assert m["id"] == "/" and m["description"]
    maskable = [i for i in m["icons"] if i.get("purpose") == "maskable"]
    assert maskable and maskable[0]["src"] != "/static/icon-512.png"

    def opaque_png(path, size):
        data = (STATIC / path).read_bytes()
        w, h = struct.unpack(">II", data[16:24])
        assert (w, h) == (size, size)
        import zlib
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
        row = 1 + size * 4
        alphas = {raw[y * row + 1 + x * 4 + 3] for y in (0, size // 2, size - 1) for x in (0, size // 2, size - 1)}
        assert alphas == {255}

    opaque_png("icon-maskable-512.png", 512)
    opaque_png("apple-touch-icon.png", 180)
    offline = anon.get("/static/offline.html")
    assert offline.status_code == 200 and "offline" in offline.text.lower() and "<script" not in offline.text
    sw = anon.get("/sw.js")
    assert sw.headers["service-worker-allowed"] == "/" and 'addEventListener("fetch"' in sw.text and '"navigate"' in sw.text
