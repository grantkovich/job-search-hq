from datetime import date, timedelta

import pytest

from jobhq import trials


def test_trial_dates_and_reminders(ctx):
    start = (date.today() - timedelta(days=20)).isoformat()
    result = trials.save(ctx, "gcp", start)
    assert result["end"] == (date.today() + timedelta(days=70)).isoformat() and result["reminders"] == 2
    texts = [r["text"] for r in ctx.reminders.pending()]
    assert all("Google Cloud free trial ends" in t and len(t) <= 200 for t in texts)
    gcp = next(t for t in trials.get(ctx) if t["name"] == "gcp")
    assert gcp["left"] == 70


def test_saving_again_replaces_the_reminders(ctx):
    trials.save(ctx, "linkedin", date.today().isoformat())
    trials.save(ctx, "linkedin", date.today().isoformat(), 14)
    assert len(ctx.reminders.pending()) == 2
    trials.save(ctx, "linkedin", "")
    assert ctx.reminders.pending() == [] and next(t for t in trials.get(ctx) if t["name"] == "linkedin")["start"] == ""


def test_past_warning_days_are_skipped(ctx):
    start = (date.today() - timedelta(days=28)).isoformat()  # LinkedIn ends in 2 days: the 5-day warning has passed
    assert trials.save(ctx, "linkedin", start)["reminders"] == 1


@pytest.mark.parametrize("start, days", [("soon", None), (date.today().isoformat(), "x"), (date.today().isoformat(), 0),
                                         ((date.today() + timedelta(days=90)).isoformat(), None)])
def test_bad_input(ctx, start, days):
    with pytest.raises(trials.TrialError):
        trials.save(ctx, "gcp", start, days)


def test_setup_form_and_today_banner(client, ctx):
    r = client.post("/setup/trials", data={"name": "gcp", "start": (date.today() - timedelta(days=85)).isoformat(), "days": "90"})
    assert r.status_code == 204 and "reminders set" in r.headers["hx-trigger"]
    assert "5 days left" in client.get("/").text
    assert "Ends" in client.get("/setup").text
    assert "Use a date" in client.post("/setup/trials", data={"name": "gcp", "start": "nope"}).headers["hx-trigger"]
