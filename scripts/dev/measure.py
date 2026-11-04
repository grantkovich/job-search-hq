"""Measure the heavy moments inside a running copy: run with
  docker compose ... exec -T app-NAME python /app/src/../measure.py   (or pipe this file to `python -`)
Prints PDF build time, a Claude run's time and memory (with a deliberately invalid API key, so it costs
nothing and shows the failure message), and the container's peak memory."""
import asyncio
import time
from pathlib import Path


def peak():
    for name in ("memory.peak", "memory.max_usage_in_bytes"):
        p = Path("/sys/fs/cgroup") / name
        if p.exists():
            return int(p.read_text()) // (1024 * 1024)
    return -1


from jobhq.ai.runner import ClaudeRunner  # noqa: E402
from jobhq.config import load  # noqa: E402
from jobhq.context import build  # noqa: E402
from jobhq.documents import build_pdf  # noqa: E402

ctx = build(load())
data = {"name": "Pat Lee", "contact": ["pat@example.com", "555-0100"], "headline": "Marketing operations", "summary": "Ops lead. " * 20,
        "experience": [{"title": "Lead", "company": f"Company {i}", "location": "Remote", "dates": "2020 - Present",
                        "bullets": ["Did a measurable thing that mattered to the business. " * 2] * 5} for i in range(4)],
        "education": [{"degree": "MBA", "school": "State U", "dates": "2024"}], "skills": ["HubSpot", "Salesforce", "SQL"] * 4}
start = time.monotonic()
pdf = build_pdf("resume", data)
print(f"pdf: {time.monotonic() - start:.1f}s, {len(pdf) // 1024} KB")
print(f"peak after pdf: {peak()} MB")

runner = ClaudeRunner(lambda: {"ANTHROPIC_API_KEY": "sk-ant-invalid-for-measurement"}, ctx.spend, ctx.config.work_dir,
                      lambda: "claude-haiku-5-5", timeout_s=120)
start = time.monotonic()
result = asyncio.run(runner.run("measure", "Reply ok.", "ok?", max_turns=1, budget_usd=0.01))
print(f"claude run: {time.monotonic() - start:.1f}s, ok={result.ok}, message={result.error!r}")
print(f"peak after claude run: {peak()} MB")
