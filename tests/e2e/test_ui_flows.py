"""Keyboard, screen-reader, phone and offline behaviour in a real browser."""
import re

import pytest

from .conftest import wait_for

pytestmark = pytest.mark.e2e


def no_problems(page, expect_4xx=False):
    problems = [p for p in page.problems if not (expect_4xx and re.search(r"status of 4\d\d|Error Code 4\d\d", p))]
    assert problems == [], problems


def active(page) -> str:
    return page.evaluate("document.activeElement === document.body ? 'BODY'"
                         " : (document.activeElement.innerText || document.activeElement.value || document.activeElement.tagName)")


def test_reload_keeps_the_message_the_place_and_the_open_job(signed_in):
    page = signed_in
    ctx = page.live.ctx
    for title in ("Cashier", "Shift Lead"):
        ctx.db.run("INSERT INTO experience (company, title) VALUES ('Corner Market', ?)", (title,))
    page.goto("/profile")
    second = page.locator("details.job").nth(1)
    second.locator("summary").click()
    second.locator("input[name=text]").fill("Trained 6 new hires on the register and stock system")
    second.get_by_role("button", name="Add fact").click()
    page.wait_for_selector(".toast >> text=Fact added.")
    assert page.locator("details.job").nth(1).get_attribute("open") is not None  # the edited job is still open
    assert page.locator("details.job").nth(1).locator("text=Trained 6 new hires").is_visible()
    assert active(page) == "Add fact"
    wait_for(lambda: page.locator("#announce").text_content() == "Fact added.", timeout=10)  # screen readers hear it too (slack for a busy machine)
    no_problems(page)


def test_reminder_message_survives_the_reload(signed_in):
    page = signed_in
    page.goto("/reminders")
    page.get_by_label("What to remind you about").fill("Call the store manager back")
    page.get_by_label("When", exact=True).select_option("tomorrow")
    page.get_by_role("button", name="Set").click()
    page.wait_for_selector("li:has-text('Call the store manager back')")
    assert "Reminder set for" in page.locator(".toast").last.inner_text()
    no_problems(page)


def test_error_toast_stays_and_marks_the_field(signed_in):
    page = signed_in
    page.goto("/profile")
    page.fill("input[name=email]", "not-an-email")
    page.evaluate("document.querySelector('input[name=email]').type = 'text'")  # get past the browser's own check
    page.get_by_role("button", name="Save basic information").click()
    page.wait_for_selector(".toast.bad")
    email = page.locator("input[name=email]")
    assert email.get_attribute("aria-invalid") == "true"
    assert "email address" in page.locator("#" + email.get_attribute("aria-describedby").split()[-1]).inner_text()
    assert active(page) == "not-an-email"
    page.wait_for_timeout(10000)
    assert page.locator(".toast.bad").count() == 1  # still there after 10 seconds
    page.get_by_role("button", name="Dismiss message").click()
    assert page.locator(".toast.bad").count() == 0
    page.fill("input[name=email]", "pat@example.com")
    page.get_by_role("button", name="Save basic information").click()
    page.wait_for_selector(".toast >> text=Saved.")
    assert email.get_attribute("aria-invalid") is None
    no_problems(page, expect_4xx=True)


def test_focus_moves_on_when_its_item_is_removed(signed_in):
    page = signed_in
    ctx = page.live.ctx
    for topic in ("Excel", "Square POS"):
        ctx.db.run("INSERT INTO profile_questions (topic, question, created) VALUES (?, ?, 0)", (topic, f"Have you used {topic}?"))
    page.goto("/interview")
    page.click("summary:has-text('Open questions')")
    page.locator("li:has-text('Have you used Excel?')").get_by_role("button", name="Skip").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("li:has-text('Have you used Excel?')", state="detached")
    wait_for(lambda: active(page) != "BODY", timeout=3)
    assert "Square POS" in active(page)
    no_problems(page)


def test_typing_an_answer_survives_grading_polls(signed_in):
    page = signed_in
    ctx = page.live.ctx
    from jobhq import practice
    qid = practice.new_question(ctx, None, "Why this job?", "motivation")
    practice.add_attempt(ctx, qid, "Because I enjoy helping customers every day.", "typed", 20)  # stays "grading": no worker job
    page.goto(f"/practice/q/{qid}")
    box = page.get_by_label("Your answer", exact=True)
    box.fill("Half-typed answer")
    page.wait_for_timeout(7000)  # two polls
    assert box.input_value() == "Half-typed answer" and active(page) == "Half-typed answer"
    no_problems(page)


@pytest.mark.parametrize("width", [320, 1024])
def test_every_menu_link_is_visible(signed_in, width):
    page = signed_in
    page.set_viewport_size({"width": width, "height": 800})
    page.goto("/")
    for name in ("Today", "Roles", "Chat", "Profile", "Interview", "Documents", "Outreach", "Practice", "Inbox", "Reminders", "Suggestions",
                 "Setup", "Settings"):
        link = page.get_by_role("link", name=name, exact=True)
        box = link.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, name
    assert page.get_by_role("button", name="Sign out").is_visible()
    no_problems(page)


@pytest.mark.parametrize("path", ["/chat", "/settings", "/intake", "/roles", "/outreach", "/documents", "/practice", "/reminders"])
def test_small_phone_has_no_sideways_scroll(signed_in, path):
    page = signed_in
    ctx = page.live.ctx
    ctx.roles.create({"company": "A very long company name with no breaks at all", "title": "Supercalifragilisticexpialidocious specialist"})
    ctx.db.run("INSERT INTO ai_runs (at, task, model, cost_usd, ok, error) VALUES (0, 'chat', 'claude-something-long-model-name', 0.01, 0, ?)",
               ("x" * 200,))
    page.set_viewport_size({"width": 320, "height": 640})
    page.goto(path)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{path} scrolls sideways by {overflow}px"
    no_problems(page)


def test_offline_page_when_the_network_is_gone(signed_in):
    page = signed_in
    page.goto("/")
    page.evaluate("navigator.serviceWorker.ready")
    wait_for(lambda: page.evaluate("caches.has('jobhq-offline-v1')"), timeout=10)
    page.reload()  # now controlled by the service worker
    page.context.set_offline(True)
    page.goto("/roles")
    assert page.get_by_role("heading", name="You're offline").is_visible()
    assert page.evaluate("caches.open('jobhq-offline-v1').then(c => c.keys()).then(k => k.map(r => new URL(r.url).pathname))") == \
        ["/static/offline.html", "/static/app.css", "/static/icon.svg"]
    page.context.set_offline(False)
