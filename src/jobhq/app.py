"""The web app for one copy: create_app() builds it from the environment."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config as config_mod
from . import documents
from .ai import tasks
from .context import Ctx, build
from .intake import Gmail, check_boards, check_inbox
from .security import GuardMiddleware
from .web import common
from .web.routes import router
from .worker import Schedule, Worker, daily_at, every_minutes

log = logging.getLogger("jobhq")
STATIC = Path(__file__).parent / "static"


def make_worker(ctx: Ctx) -> Worker:
    gmail = Gmail(ctx)
    ctx.extras["gmail"] = gmail
    w = Worker(ctx.queue, ctx.config.tz, schedules=[
        Schedule("reminders", every_minutes(1)),
        Schedule("inbox", every_minutes(60, start_hour=7, end_hour=22)),
        Schedule("boards", daily_at(6, 30)),
        Schedule("digest", daily_at(7, 30)),
        Schedule("cleanup", daily_at(3, 15)),
    ])

    @w.on("chat")
    async def _chat(p):
        return await tasks.chat_reply(ctx, int(p["message_id"]))

    @w.on("score")
    async def _score(p):
        return await tasks.score_role(ctx, int(p["role_id"]))

    @w.on("draft")
    async def _draft(p):
        try:
            doc_id = await documents.create(ctx, int(p["role_id"]), p["kind"])
        except documents.DocumentError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "url": f"/documents/{doc_id}"}

    @w.on("outreach_draft")
    async def _outreach(p):
        from .ai.runner import AIUnavailable
        from .outreach import Outreach, OutreachError
        try:
            await Outreach(ctx).draft(int(p["contact_id"]), p["kind"])
        except (OutreachError, AIUnavailable) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @w.on("grade")
    async def _grade(p):
        return await tasks.grade_answer(ctx, int(p["attempt_id"]))

    @w.on("reminders")
    async def _reminders(_p):
        return {"sent": await asyncio.to_thread(ctx.reminders.fire_due)}

    @w.on("inbox")
    async def _inbox(_p):
        counts = await check_inbox(ctx, gmail)
        if counts.get("applied") or counts.get("review") or counts.get("new_roles"):
            parts = [f"{counts['new_roles']} new roles" if counts.get("new_roles") else "",
                     f"{counts['applied']} updates" if counts.get("applied") else "",
                     f"{counts['review']} to review" if counts.get("review") else ""]
            await asyncio.to_thread(ctx.push.send, "Inbox", ", ".join(p for p in parts if p), "/intake", tag="inbox")
        return counts

    @w.on("boards")
    async def _boards(_p):
        return await asyncio.to_thread(check_boards, ctx)

    @w.on("digest")
    async def _digest(_p):
        tasks.release_questions(ctx)  # tool questions that waited past an earlier day's cap of five
        board, due = ctx.roles.board(), ctx.roles.due()
        bits = []
        if due["interviews"]:
            bits.append(f"{len(due['interviews'])} interview{'s' if len(due['interviews']) > 1 else ''} this week")
        if due["next_steps"]:
            bits.append(f"{len(due['next_steps'])} next step{'s' if len(due['next_steps']) > 1 else ''} due")
        if due["followups"]:
            bits.append(f"{len(due['followups'])} follow-up{'s' if len(due['followups']) > 1 else ''}")
        if board["ready"]:
            bits.append(f"{len(board['ready'])} ready to apply")
        if ctx.proposals.count():
            bits.append(f"{ctx.proposals.count()} suggestions to approve")
        body = "; ".join(bits) or "A quiet day. Nothing is due."
        await asyncio.to_thread(ctx.push.send, f"Good morning — {datetime.now(ctx.config.tz):%A}", body, "/", tag="digest")
        return {"body": body}

    @w.on("cleanup")
    async def _cleanup(_p):
        ctx.sessions.cleanup()
        ctx.queue.prune()
        return {"ok": True}

    return w


def create_app(ctx: Ctx | None = None, run_worker: bool = True) -> FastAPI:
    if ctx is None:
        ctx = build(config_mod.load())
    common.install_filters(ctx)
    worker = make_worker(ctx)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(worker.loop(concurrency=ctx.config.workers)) if run_worker else None
        yield
        if task:
            worker.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, timeout=10)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx
    app.state.worker = worker
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/healthz", include_in_schema=False)
    def health():
        try:
            ctx.db.value("SELECT 1")
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.exception_handler(common.SignedOut)
    async def signed_out(request: Request, _exc):
        if common.is_htmx(request):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return Response(status_code=303, headers={"Location": "/login"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if common.is_htmx(request):
            return common.error(str(exc.detail), exc.status_code)
        return HTMLResponse(f"<!doctype html><title>{exc.status_code}</title><p>{'Not found' if exc.status_code == 404 else 'Something went wrong'}."
                            "</p><p><a href='/'>Back to Job Search HQ</a></p>", status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        if common.is_htmx(request):
            return common.error("Something went wrong. It was logged.", 500)
        return HTMLResponse("<!doctype html><title>Error</title><p>Something went wrong. It was logged.</p><p><a href='/'>Back</a></p>",
                            status_code=500)

    app.add_middleware(GuardMiddleware, config=ctx.config, sessions=ctx.sessions)
    hosts = [ctx.config.host] + (["testserver"] if ctx.config.host in ("localhost", "127.0.0.1") else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts + ["localhost", "127.0.0.1"])  # the container health check
    return app
