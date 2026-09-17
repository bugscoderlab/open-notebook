#!/usr/bin/env python3
"""Regenerate docs/7-DEVELOPMENT/*/schedule.md from live GitHub issue dependencies.

For every schedule file (docs/7-DEVELOPMENT/*/schedule.md), reads the ticket list
from its frontmatter, fetches each issue's title/state and native blocking edges
(GET /repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by), recomputes
topological waves, and rewrites the generated section between the marker comments.

Runs locally (uses the developer's `gh` auth) and in GitHub Actions (GH_TOKEN).

Usage: python3 render_schedule.py [--dry-run] [--only <feature-slug>]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULES_GLOB = "docs/7-DEVELOPMENT/*/schedule.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED SCHEDULE -->"
END_MARKER = "<!-- END GENERATED SCHEDULE -->"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TICKETS_RE = re.compile(r"^tickets:\s*\[([^\]]*)\]", re.M)
NUMBER_RE = re.compile(r"\d+")
DAY_SECONDS = 86400


@dataclass
class Ticket:
    number: int
    title: str
    state: str  # "open" | "closed" | "unknown"
    blocked_by: list[int]


def die(msg: str) -> NoReturn:
    print(f"render_schedule: {msg}", file=sys.stderr)
    sys.exit(1)


def repo_slug() -> str:
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        die(f"cannot read git remote: {proc.stderr.strip()}")
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", proc.stdout.strip())
    if not match:
        die(f"cannot parse GitHub remote: {proc.stdout.strip()}")
    return f"{match.group(1)}/{match.group(2)}"


def gh_api(path: str):
    proc = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"gh api {path} failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def fetch_ticket(slug: str, number: int, cache: dict[int, Ticket]) -> Ticket:
    if number in cache:
        return cache[number]
    issue = gh_api(f"repos/{slug}/issues/{number}")
    raw_blockers = gh_api(f"repos/{slug}/issues/{number}/dependencies/blocked_by")
    blockers = []
    for entry in raw_blockers:
        if isinstance(entry, dict):
            num = entry.get("number")
        else:
            num = entry
        if isinstance(num, int):
            blockers.append(num)
    ticket = Ticket(
        number=number,
        title=issue.get("title") or f"#{number}",
        state=issue.get("state") or "unknown",
        blocked_by=blockers,
    )
    cache[number] = ticket
    return ticket


def compute_waves(open_numbers: set[int], blocked_by: dict[int, list[int]]) -> tuple[list[list[int]], list[int]]:
    """Topological levels. Only open blockers inside the ticket set gate placement;
    external/closed blockers are surfaced separately, not scheduled here."""
    remaining = set(open_numbers)
    waves: list[list[int]] = []
    while remaining:
        ready = sorted(n for n in remaining if not any(b in remaining for b in blocked_by[n]))
        if not ready:  # dependency cycle
            return waves, sorted(remaining)
        waves.append(ready)
        remaining -= set(ready)
    return waves, []


def mermaid_label(ticket: Ticket) -> str:
    title = re.sub(r'["\n\r]+', " ", ticket.title).strip()
    title = re.sub(r"\s+", " ", title)
    if len(title) > 48:
        title = title[:45].rstrip() + "..."
    return f'["#{ticket.number} {title}"]'


def render_generated(
    slug: str,
    tickets: dict[int, Ticket],
    waves: list[list[int]],
    cycle: list[int],
    external_open: dict[int, list[int]],
) -> str:
    lines: list[str] = []
    lines.append("```mermaid")
    lines.append("flowchart LR")
    for i, wave in enumerate(waves):
        lines.append(f'  subgraph "Wave {i}"')
        for number in wave:
            lines.append(f"    n{number}{mermaid_label(tickets[number])}")
        lines.append("  end")
    if cycle:
        lines.append('  subgraph "Unscheduled — dependency cycle?"')
        for number in cycle:
            lines.append(f"    n{number}{mermaid_label(tickets[number])}")
        lines.append("  end")
    for number in sorted(tickets):
        for blocker in tickets[number].blocked_by:
            if blocker in tickets:
                lines.append(f"  n{blocker} --> n{number}")
    lines.append("```")
    lines.append("")
    lines.append("```mermaid")
    lines.append("gantt")
    lines.append(f"  title {slug} — one day per wave")
    lines.append("  dateFormat X")
    for i in range(len(waves)):
        lines.append(f"  section Wave {i}")
        lines.append(f"  W{i} :{i * DAY_SECONDS}, {(i + 1) * DAY_SECONDS}")
    if cycle:
        lines.append("  section Cycle")
        lines.append(f"  CY :{len(waves) * DAY_SECONDS}, {(len(waves) + 1) * DAY_SECONDS}")
    lines.append("```")
    lines.append("")
    lines.append("| Wave | Tickets | Open external blockers |")
    lines.append("|---|---|---|")
    for i, wave in enumerate(waves):
        ext = sorted({b for n in wave for b in external_open.get(n, [])})
        ext_text = ", ".join(f"#{b}" for b in ext) if ext else "—"
        tickets_text = ", ".join(f"#{n}" for n in wave)
        lines.append(f"| {i} | {tickets_text} | {ext_text} |")
    if cycle:
        tickets_text = ", ".join(f"#{n}" for n in cycle)
        lines.append(f"| ⚠️ cycle | {tickets_text} | resolve blocking edges |")
    return "\n".join(lines)


def splice(text: str, generated: str) -> str:
    block = f"{BEGIN_MARKER}\n{generated}\n{END_MARKER}"
    if BEGIN_MARKER in text and END_MARKER in text:
        pre = text[: text.index(BEGIN_MARKER)]
        post = text[text.index(END_MARKER) + len(END_MARKER):]
        return pre + block + post
    return text.rstrip("\n") + "\n\n" + block + "\n"


def process_file(path: Path, slug: str, dry_run: bool) -> bool:
    text = path.read_text()
    fm = FRONTMATTER_RE.match(text)
    if not fm:
        die(f"{path}: no frontmatter block")
    tm = TICKETS_RE.search(fm.group(1))
    if not tm:
        die(f"{path}: frontmatter has no 'tickets: […]' list")
    ticket_numbers = [int(n) for n in NUMBER_RE.findall(tm.group(1))]
    if not ticket_numbers:
        die(f"{path}: tickets list is empty")

    cache: dict[int, Ticket] = {}
    for number in ticket_numbers:
        fetch_ticket(slug, number, cache)

    # External blockers: fetch their state so closed ones don't gate or warn.
    external_numbers = sorted(
        {b for n in ticket_numbers for b in cache[n].blocked_by if b not in cache}
    )
    for number in external_numbers:
        fetch_ticket(slug, number, cache)

    tickets = {n: cache[n] for n in ticket_numbers}
    open_numbers = {n for n in ticket_numbers if tickets[n].state != "closed"}
    blocked_by = {n: tickets[n].blocked_by for n in open_numbers}
    waves, cycle = compute_waves(open_numbers, blocked_by)
    external_open = {
        n: [b for b in tickets[n].blocked_by if b not in tickets and cache[b].state != "closed"]
        for n in ticket_numbers
    }

    generated = render_generated(path.parent.name, tickets, waves, cycle, external_open)
    new_text = splice(text, generated)
    if new_text == text:
        print(f"up to date: {path}")
        return False
    if dry_run:
        print(f"--- would update {path}")
        print(generated)
    else:
        path.write_text(new_text)
        print(f"updated: {path}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    parser.add_argument("--only", metavar="SLUG", help="process only this feature directory")
    args = parser.parse_args()

    slug = repo_slug()
    files = sorted(REPO_ROOT.glob(SCHEDULES_GLOB))
    if args.only:
        files = [f for f in files if f.parent.name == args.only]
    if not files:
        print("no schedule files found; nothing to do")
        return

    changed = any(process_file(path, slug, args.dry_run) for path in files)
    if not changed:
        print("all schedules up to date")


if __name__ == "__main__":
    main()
