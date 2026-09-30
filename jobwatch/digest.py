"""Build the digest as Markdown (GitHub summary / file), HTML (email) and CSV.

Layout:
  - Highlighted postings (matching `highlight_locations`, e.g. remote) first, then everything else.
  - Within each section: High priority, then Main list, then company name.
  - Identical titles at one company are collapsed into one line with a count and extra links.
  - Each line says which resume version to send, when `resume_rules` are configured.
"""
import csv
import html
import os
import re
from collections import OrderedDict
from typing import List, Tuple

from .models import Job

HIGHLIGHT_TITLE = "Remote"      # section heading; config: highlight_title
HIGHLIGHT_SUMMARY = "remote"    # subject line, "(3 remote)"; config: highlight_summary
OTHER_TITLE = "Other locations"
MAX_EXTRA_LINKS = 10
MAX_LOCATIONS = 3


def sort_jobs(jobs: List[Job]) -> List[Job]:
    return sorted(jobs, key=lambda j: (j.priority != "High", j.list != "Main", j.company.lower(), j.title.lower()))


def _company_label(job: Job) -> str:
    bits = [job.list]
    if job.priority:
        bits.insert(0, f"{job.priority} priority")
    return f"{job.company} ({' · '.join(bits)})"


def _norm_title(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip().lower())
    return t.replace("full stack", "full-stack")


def _sections(jobs: List[Job], highlight_title: str = HIGHLIGHT_TITLE):
    """[(section title or None, [(company label, [[job, job...] per distinct title]) ...]) ...]"""
    ordered = sort_jobs(jobs)
    near = [j for j in ordered if j.highlight]
    other = [j for j in ordered if not j.highlight]
    parts = [(highlight_title, near), (OTHER_TITLE, other)] if near else [(None, other)]
    out = []
    for title, section_jobs in parts:
        if not section_jobs:
            continue
        companies = OrderedDict()
        for j in section_jobs:
            companies.setdefault(j.company, OrderedDict()).setdefault(_norm_title(j.title), []).append(j)
        out.append((title, [(_company_label(groups[next(iter(groups))][0]), list(groups.values()))
                            for groups in companies.values()]))
    return out


def _locations(group: List[Job]) -> str:
    seen = []
    for j in group:
        if j.location and j.location not in seen:
            seen.append(j.location)
    text = "; ".join(seen[:MAX_LOCATIONS])
    if len(seen) > MAX_LOCATIONS:
        text += f" +{len(seen) - MAX_LOCATIONS} more"
    return text


def subject(new: List[Job], day: str, highlight_summary: str = HIGHLIGHT_SUMMARY) -> str:
    if not new:
        return f"Job watch {day}: no new postings"
    companies = len({j.company for j in new})
    text = (f"Job watch {day}: {len(new)} new posting{'s' if len(new) != 1 else ''} "
            f"at {companies} compan{'ies' if companies != 1 else 'y'}")
    near = sum(1 for j in new if j.highlight)
    return f"{text} ({near} {highlight_summary})" if near else text


def _failures_note(failures) -> str:
    n = len(failures)
    return f"{n} compan{'ies' if n != 1 else 'y'} could not be checked today"


# ------------------------------------------------------------------ Markdown
def _md_line(group: List[Job]) -> str:
    first = group[0]
    loc = _locations(group)
    resume = f" · Resume: {first.resume}" if first.resume else ""
    if len(group) == 1:
        return f"- [{first.title}]({first.url})" + (f" — {loc}" if loc else "") + resume
    extra = " ".join(f"[{i}]({j.url})" for i, j in enumerate(group[1:1 + MAX_EXTRA_LINKS], start=2))
    more = f" +{len(group) - 1 - MAX_EXTRA_LINKS} more" if len(group) - 1 > MAX_EXTRA_LINKS else ""
    return (f"- [{first.title}]({first.url}) — {len(group)} openings" + (f" ({loc})" if loc else "")
            + f" · also {extra}{more}" + resume)


def build_markdown(new: List[Job], failures: List[Tuple[str, str]], stats: dict, day: str,
                   highlight_title: str = HIGHLIGHT_TITLE, highlight_summary: str = HIGHLIGHT_SUMMARY) -> str:
    lines = [f"# {subject(new, day, highlight_summary)}", "",
             f"Checked {stats.get('companies', 0)} companies, {stats.get('matching', 0)} matching open roles in total.", ""]
    sections = _sections(new, highlight_title)
    for title, companies in sections:
        count = sum(len(g) for _, groups in companies for g in groups)
        head = "###" if title else "##"
        if title:
            lines += [f"## {title} ({count})", ""]
        for label, groups in companies:
            lines.append(f"{head} {label}")
            lines += [_md_line(g) for g in groups]
            lines.append("")
    if failures:
        lines += [f"<details><summary>{_failures_note(failures)}</summary>", ""]
        lines += [f"- {name}: {err}" for name, err in sorted(failures)]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ HTML (email)
def _html_line(group: List[Job]) -> str:
    e = html.escape
    first = group[0]
    loc = _locations(group)
    parts = [f'<a href="{e(first.url)}">{e(first.title)}</a>']
    if len(group) > 1:
        parts.append(f' <b>· {len(group)} openings</b>')
    if loc:
        parts.append(f' <span style="color:#666">— {e(loc)}</span>')
    if len(group) > 1:
        links = " ".join(f'<a href="{e(j.url)}">{i}</a>' for i, j in enumerate(group[1:1 + MAX_EXTRA_LINKS], start=2))
        more = f" +{len(group) - 1 - MAX_EXTRA_LINKS} more" if len(group) - 1 > MAX_EXTRA_LINKS else ""
        parts.append(f' <span style="color:#888">· also {links}{e(more)}</span>')
    if first.resume:
        parts.append(f' <span style="background:#EEF3FB;color:#1F3864;border-radius:3px;padding:0 4px;'
                     f'font-size:12px">{e(first.resume)} resume</span>')
    return '<li style="margin:3px 0">' + "".join(parts) + "</li>"


def build_html(new: List[Job], failures: List[Tuple[str, str]], stats: dict, day: str,
               highlight_title: str = HIGHLIGHT_TITLE, highlight_summary: str = HIGHLIGHT_SUMMARY) -> str:
    e = html.escape
    out = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222">',
           f'<h2 style="margin:0 0 6px">{e(subject(new, day, highlight_summary))}</h2>',
           f'<p style="color:#555;margin:0 0 12px">Checked {stats.get("companies", 0)} companies, '
           f'{stats.get("matching", 0)} matching open roles in total.</p>']
    for title, companies in _sections(new, highlight_title):
        if title:
            count = sum(len(g) for _, groups in companies for g in groups)
            out.append(f'<h2 style="margin:22px 0 4px;font-size:17px;border-bottom:2px solid #1F3864;'
                       f'padding-bottom:3px">{e(title)} ({count})</h2>')
        for label, groups in companies:
            color = "#C00000" if "High priority" in label else "#1F3864"
            out.append(f'<h3 style="margin:14px 0 4px;color:{color};font-size:15px">{e(label)}</h3>'
                       '<ul style="margin:0;padding-left:18px">')
            out += [_html_line(g) for g in groups]
            out.append("</ul>")
    if failures:
        out.append(f'<p style="color:#888;margin-top:20px">{e(_failures_note(failures))}: '
                   + e(", ".join(sorted(n for n, _ in failures))) + "</p>")
    out.append("</div>")
    return "\n".join(out)


# ------------------------------------------------------------------ CSV
CSV_FIELDS = ["date", "company", "list", "priority", "title", "resume", "highlighted", "tracks",
              "location", "posted_at", "url"]
TRACK_LABELS = {"backend": "Backend", "data": "Data", "ai_ml": "AI/ML"}


def _row(j: Job, day: str) -> dict:
    return {"date": day, "company": j.company, "list": j.list, "priority": j.priority, "title": j.title,
            "resume": j.resume, "highlighted": "yes" if j.highlight else "",
            "tracks": ", ".join(TRACK_LABELS.get(t, t) for t in j.tracks), "location": j.location,
            "posted_at": j.posted_at.isoformat() if j.posted_at else "", "url": j.url}


def write_csv(path: str, jobs: List[Job], day: str, append: bool = False) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    old_rows = []
    if append and os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if (reader.fieldnames or []) == CSV_FIELDS:
                old_rows = None                      # same columns: just append
            else:
                old_rows = list(reader)              # older file: rewrite it with the new columns
    mode = "a" if (append and old_rows is None) else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if mode == "w":
            w.writeheader()
            for r in old_rows or []:
                w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
        for j in sort_jobs(jobs):
            w.writerow(_row(j, day))
