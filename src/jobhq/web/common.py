"""Shared pieces for the page routes: templates, the signed-in check, redirects, formatting."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup

from ..auth import Session, cookie_name
from ..context import Ctx

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
TEMPLATES.env.autoescape = True
_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).disable(["image"])


def _link_open(tokens, idx, options, env):
    tokens[idx].attrSet("rel", "noopener noreferrer nofollow")
    tokens[idx].attrSet("target", "_blank")
    return _md.renderer.renderToken(tokens, idx, options, env)


_md.renderer.rules["link_open"] = _link_open


def _safe_link(url: str) -> bool:
    """Only http(s), mailto and this site's own paths. Anything else (javascript:, data:, tel:, ms-settings:,
    intent:, //host, /\\host) renders as plain text."""
    low = url.strip().lower()
    if low.startswith(("http://", "https://", "mailto:")):
        return True
    return same_site_path(url)


_md.validateLink = _safe_link


def same_site_path(url: str) -> bool:
    """A path on this site. Browsers treat "/\\host" like "//host" and drop tabs and line breaks, so those are refused too."""
    return (isinstance(url, str) and url.startswith("/") and not url.startswith("//") and "\\" not in url
            and "%5c" not in url.lower() and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in url))


def markdown(text: str) -> Markup:
    """AI text as safe HTML: raw HTML is escaped, images are off, links open in a new tab, and only http(s)/mailto links are allowed."""
    return Markup(_md.render(text or ""))  # noqa: S704  # nosec B704 - markdown-it with html disabled escapes everything


class SignedOut(Exception):
    pass


def ctx_of(request: Request) -> Ctx:
    return request.app.state.ctx


def session_of(request: Request) -> Session | None:
    ctx = ctx_of(request)
    return ctx.sessions.lookup(request.cookies.get(cookie_name(ctx.config)))


def require(request: Request) -> Session:
    """Dependency for every signed-in route."""
    session = session_of(request)
    if session is None:
        raise SignedOut()
    request.state.session = session
    return session


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def redirect(request: Request, url: str) -> Response:
    if not same_site_path(url):
        url = "/"
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def refresh(request: Request, message: str = "") -> Response:
    """Reload the page. app.js carries `message` across the reload as a toast (so screen readers hear what
    changed) and puts focus back on the control that was used."""
    import json
    headers = {"HX-Refresh": "true"}
    if message:
        headers["HX-Trigger"] = json.dumps({"jobhq:toast": message})
    return Response(status_code=204, headers=headers)


def toast(message: str, status: int = 200, html: str = "") -> HTMLResponse:
    import json
    return HTMLResponse(html, status_code=status, headers={"HX-Trigger": json.dumps({"jobhq:toast": message})})


def error(message: str, status: int = 400, fields: dict | None = None) -> HTMLResponse:
    """An error toast that stays until dismissed. `fields` ({name: problem}) also marks those fields in the
    form that was sent, with the problem written under each one."""
    import json
    trigger: dict = {"jobhq:error": message}
    if fields:
        trigger["jobhq:fields"] = {"fields": {str(k): str(v) for k, v in fields.items()}}
    return HTMLResponse("", status_code=status, headers={"HX-Trigger": json.dumps(trigger), "HX-Reswap": "none"})


def page(request: Request, template: str, **values) -> HTMLResponse:
    ctx = ctx_of(request)
    session = getattr(request.state, "session", None)
    values.setdefault("title", "")
    return TEMPLATES.TemplateResponse(request, template, {
        "ctx": ctx, "csrf": session.csrf if session else "", "signed_in": session is not None,
        "pending_suggestions": ctx.proposals.count() if session else 0, "path": request.url.path, **values})


def fragment(request: Request, template: str, **values) -> HTMLResponse:
    return page(request, template, **values)


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def when(ts, tz, fmt: str = "%b %-d, %-I:%M %p") -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz).strftime(fmt)


def money(n) -> str:
    return f"${int(n):,}" if n else ""


def pay_range(low, high=None, currency: str = "") -> str:
    """Yearly pay in the unit people think in: '$35,360–$39,520 a year (about $17–$19 an hour)' up to $100,000,
    plain yearly above that. Uses the role's currency when known."""
    from ..pay import money_text

    low, high = low or high, high or low
    if not low:
        return ""
    yearly = money_text(low, currency) + (f"–{money_text(high, currency)}" if high != low else "") + " a year"
    if high > 100_000:
        return yearly
    def per_hour(yearly: float) -> float:  # an exact rate (typed as 17.50/hr) keeps its cents; a converted salary rounds
        rate = yearly / 2080
        return round(rate, 2) if abs(rate * 100 - round(rate * 100)) < 1e-6 else round(rate)

    hourly = money_text(per_hour(low), currency) + (f"–{money_text(per_hour(high), currency)}" if high != low else "")
    return f"{yearly} (about {hourly} an hour)"


def install_filters(ctx: Ctx) -> None:
    TEMPLATES.env.filters["md"] = markdown
    TEMPLATES.env.filters["when"] = lambda ts, fmt="%b %-d, %-I:%M %p": when(ts, ctx.config.tz, fmt)
    TEMPLATES.env.filters["money"] = money
    TEMPLATES.env.filters["pay_range"] = pay_range
    TEMPLATES.env.globals["voice_enabled"] = ctx.config.voice
