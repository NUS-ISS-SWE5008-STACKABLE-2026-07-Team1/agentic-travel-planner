"""Render every workflow job for one commit onto a single timeline.

GitHub has no cross-workflow view: its run graph is per-workflow. This asks the
Actions API for every run on a commit, then every job in those runs, and draws
them against one clock.

    python make_timeline.py [<sha>] [-o out.html]

Read-only: it only ever GETs through `gh api`.
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime

REPO = "NUS-ISS-SWE5008-STACKABLE-2026-07-Team1/agentic-travel-planner"


def gh(path: str) -> dict:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"gh api {path} failed:\n{out.stderr.strip()}")
    return json.loads(out.stdout)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect(sha: str, event: str | None) -> tuple[list[dict], dict]:
    q = f"repos/{REPO}/actions/runs?head_sha={sha}&per_page=100"
    if event:
        q += f"&event={event}"
    runs = gh(q)["workflow_runs"]
    if not runs:
        sys.exit(f"No workflow runs found for {sha}.")
    rows = []
    for run in runs:
        for job in gh(run["jobs_url"].split("github.com/")[-1])["jobs"]:
            if not job.get("started_at"):
                continue
            rows.append({
                "workflow": run["name"],
                "event": run["event"],
                "job": job["name"],
                "start": ts(job["started_at"]),
                "end": ts(job["completed_at"]) if job.get("completed_at") else None,
                "conclusion": job.get("conclusion") or "running",
                "url": job.get("html_url") or run["html_url"],
            })
    meta = {
        "sha": sha,
        "branch": runs[0].get("head_branch", "?"),
        "message": (runs[0].get("head_commit") or {}).get("message", "").splitlines()[0],
        "when": min(r["start"] for r in rows),
    }
    return rows, meta


CSS = """
:root{--blue:#2F5FE9;--chip:#E8EAFB;--teal:#4FD9B8;--navy:#0F2A47;--ink:#101F33;
      --muted:#667085;--border:#E3E8F2;--grey:#9AA7BE;--panel:#F7F9FC;--bg:#fff;--card:#fff;
      --ok:#2F5FE9;--adv:#9AA7BE;--bad:#B3261E}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --ink:#E8EDF6;--muted:#A4B0C4;--border:#243352;--grey:#8192AC;--panel:#131D30;
  --bg:#0B1220;--card:#0F1828;--chip:#1B2742}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);padding:28px 16px 60px;
     font:15px/1.6 Calibri,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
.eyebrow{font:700 11px/1 Calibri;letter-spacing:.14em;text-transform:uppercase;color:var(--blue);margin:0 0 6px}
h1{font:700 27px/1.25 Arial,sans-serif;margin:0 0 6px}
.lede{color:var(--muted);margin:0 0 20px}
code{background:var(--panel);border:1px solid var(--border);border-radius:4px;padding:1px 5px;
     font:13px ui-monospace,Consolas,monospace}
.legend{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin:0 0 18px}
.sw{display:inline-block;width:22px;height:9px;border-radius:3px;vertical-align:0;margin-right:7px}
.chart{border:1px solid var(--border);border-radius:14px;background:var(--card);padding:18px 18px 10px}
.axis{position:relative;height:22px;margin-left:var(--lw);border-bottom:1px solid var(--border)}
.tick{position:absolute;top:0;font:11px Calibri;color:var(--grey);transform:translateX(-50%)}
.tick b{display:block;width:1px;height:7px;background:var(--border);margin:13px auto 0}
.grp{font:700 11px/1 Calibri;letter-spacing:.1em;text-transform:uppercase;color:var(--blue);
     margin:16px 0 6px}
.grp span{color:var(--grey);letter-spacing:.04em;text-transform:none;font-weight:700;margin-left:8px}
.row{display:flex;align-items:center;height:28px}
.lbl{width:var(--lw);flex:0 0 var(--lw);padding-right:12px;font-size:12.5px;color:var(--ink);
     white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{position:relative;flex:1;height:14px;background:var(--panel);border-radius:7px}
.bar{position:absolute;top:0;height:14px;border-radius:7px;background:var(--ok);min-width:3px}
.bar.adv{background:var(--adv)}
.bar.bad{background:var(--bad)}
.bar a{display:block;height:100%}
.dur{position:absolute;top:-1px;font:11px Calibri;color:var(--muted);white-space:nowrap}
table{border-collapse:collapse;width:100%;margin:26px 0 0;font-size:13.5px}
th,td{border:1px solid var(--border);padding:7px 10px;text-align:left}
th{background:var(--panel);font:700 12px Arial,sans-serif}
.note{border:1px solid var(--border);border-left:4px solid var(--blue);border-radius:8px;
      background:var(--card);padding:12px 14px;margin:22px 0 0}
.note p{margin:0 0 8px}.note p:last-child{margin:0}
.small{font-size:12.5px;color:var(--muted)}
footer{margin-top:30px;padding-top:12px;border-top:1px solid var(--border);color:var(--grey);font-size:12px}
"""


def render(rows: list[dict], meta: dict) -> str:
    t0 = min(r["start"] for r in rows)
    t1 = max((r["end"] or r["start"]) for r in rows)
    span = max((t1 - t0).total_seconds(), 1)

    # Order groups by when their first job started, jobs likewise.
    groups: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["start"]):
        groups.setdefault(r["workflow"], []).append(r)
    ordered = sorted(groups.items(), key=lambda kv: min(r["start"] for r in kv[1]))

    step = 60 if span > 360 else 30
    ticks = []
    t = 0
    while t <= span:
        ticks.append((t / span * 100, f"{int(t // 60)}m" if t else "0"))
        t += step

    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline timeline</title><style>{CSS}</style></head><body><div class="wrap">
<p class="eyebrow">Atlas AI Travel &middot; CI/CD</p>
<h1>What ran, and when</h1>
<p class="lede">Every workflow job for one commit on a single clock &mdash;
the view GitHub does not provide, because its run graph is per&#8209;workflow.</p>
<div class="legend">
  <span><span class="sw" style="background:var(--ok)"></span>passed</span>
  <span><span class="sw" style="background:var(--adv)"></span>advisory / skipped</span>
  <span><span class="sw" style="background:var(--bad)"></span>failed</span>
</div>
<div class="chart" style="--lw:250px">
<div class="axis">"""]
    for pct, label in ticks:
        out.append(f'<div class="tick" style="left:{pct:.2f}%">{label}<b></b></div>')
    out.append("</div>")

    for wf, jobs in ordered:
        ev = jobs[0]["event"]
        out.append(f'<p class="grp">{html.escape(wf)}<span>on: {html.escape(ev)}</span></p>')
        for j in jobs:
            a = (j["start"] - t0).total_seconds()
            b = ((j["end"] or t1) - t0).total_seconds()
            left = a / span * 100
            width = max((b - a) / span * 100, 0.4)
            cls = {"success": "", "failure": "bad", "cancelled": "bad"}.get(j["conclusion"], "adv")
            secs = int(b - a)
            dur = f"{secs//60}m {secs%60}s" if secs >= 60 else f"{secs}s"
            # keep the duration label inside the chart
            side = (f'left:calc({left:.2f}% + {width:.2f}% + 8px)' if left + width < 82
                    else f'right:calc({100-left:.2f}% + 8px)')
            out.append(
                f'<div class="row"><div class="lbl" title="{html.escape(j["job"])}">'
                f'{html.escape(j["job"])}</div><div class="track">'
                f'<div class="bar {cls}" style="left:{left:.2f}%;width:{width:.2f}%">'
                f'<a href="{html.escape(j["url"])}" title="{html.escape(j["job"])} &mdash; {dur}"></a></div>'
                f'<div class="dur" style="{side}">{dur}</div></div></div>')
    out.append("</div>")

    total = int(span)
    out.append(f"""
<table><tbody>
<tr><th>Commit</th><td><code>{meta['sha'][:7]}</code> on <code>{html.escape(meta['branch'])}</code></td></tr>
<tr><th>Message</th><td>{html.escape(meta['message'])}</td></tr>
<tr><th>Started</th><td>{t0.strftime('%Y-%m-%d %H:%M:%S')} UTC</td></tr>
<tr><th>Total</th><td>{total//60}m {total%60}s, across {len(ordered)} workflows and {len(rows)} jobs</td></tr>
</tbody></table>

<div class="note">
<p><b>What to read off it.</b> Bars starting at the same point ran in parallel. A bar that starts
later either depends on something (<code>needs:</code>) or was queued waiting for a runner.</p>
<p class="small">A long bar is not necessarily work: the deployment's first job spends almost all of
its time <i>waiting</i> for the other workflows to conclude, because GitHub cannot chain across
workflows and it has to poll the API instead.</p>
</div>

<footer>Generated from the GitHub Actions API. Bars link to their job logs.</footer>
</div></body></html>""")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sha", nargs="?", help="commit SHA (default: head of origin/release)")
    ap.add_argument("-o", "--out", default="cicd-timeline.html")
    ap.add_argument("--event", default="push", help="filter by event; empty string for all")
    args = ap.parse_args()

    sha = args.sha or subprocess.run(["git", "rev-parse", "origin/release"],
                                     capture_output=True, text=True).stdout.strip()
    rows, meta = collect(sha, args.event or None)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(rows, meta))
    print(f"{args.out}: {len(rows)} jobs across "
          f"{len({r['workflow'] for r in rows})} workflows for {sha[:7]}")


if __name__ == "__main__":
    main()
