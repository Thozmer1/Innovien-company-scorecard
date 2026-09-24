#!/usr/bin/env python3
"""Assemble weekly_data.json from Comtrak Raw Notion query results.

Companion to rebuild_queries.json. The scheduled task runs each query in that file via the
Notion connector and drops its `results` array at <qdir>/<key>.json; this script reads those,
applies the same logic as lib/rebuild.mjs, and writes weekly_data.json (hybrid: Hours + Spread
overlay their fresher "(API)" twin). Live-board figures (company.weekly_spread, current-week
lock-up total) are carried forward. Raffle state is preserved + advanced.

Usage:  python assemble_weekly.py [qdir]        (qdir default: /tmp/qres)
        python assemble_weekly.py [qdir] --dry-run   (print diff, do not write)
"""
import json, os, sys, math
from datetime import datetime, timezone, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # innovien-scorecard/
QDIR = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/tmp/qres"
DRY  = "--dry-run" in sys.argv

def load(p):
    with open(p) as f: return json.load(f)
def q(key):
    p = os.path.join(QDIR, key + ".json")
    if not os.path.exists(p): raise SystemExit(f"MISSING query result: {p}")
    d = load(p)
    return d.get("results", d) if isinstance(d, dict) else d   # accept raw array or {results:[]}

goals = load(os.path.join(ROOT, "goals.json"))
prev  = load(os.path.join(ROOT, "weekly_data.json"))

TODAY = datetime.now(timezone.utc).date()
QS = date.fromisoformat(goals.get("quarterStart", "2026-06-29"))
QE = date.fromisoformat(goals.get("quarterEnd", "2026-09-27"))
warnings = []
if QS != date(2026, 6, 29) or QE != date(2026, 9, 27):
    warnings.append(f"goals quarter ({QS}..{QE}) != the dates hardcoded in rebuild_queries.json (2026-06-29..2026-09-27) — regenerate the queries for the new quarter.")
WK_MON = TODAY - timedelta(days=TODAY.weekday())          # Monday of this week
weeks_left = ((QE - WK_MON).days + 1) / 7.0
denom = max(1.0, weeks_left - 2.5)

def _nk(s):  # normalized name key (collapse whitespace, casefold)
    return " ".join(str(s or "").split()).lower()

def merge_rows(rows, sum_fields, name_key="name"):
    """Collapse rows that are the same person under different spacing/casing."""
    out, idx = [], {}
    for r in rows:
        k = _nk(r.get(name_key))
        if k in idx:
            t = out[idx[k]]
            for f in sum_fields: t[f] = t.get(f, 0) + r.get(f, 0)
        else:
            idx[k] = len(out); out.append(dict(r))
    return out

def n(x):  return 0 if x is None else x
def rnd(x): return int(round(n(x)))
def r1(x): return round(n(x), 1)
def qopt(key):
    """Detail queries are optional — a missing file keeps whatever the previous file had."""
    pth = os.path.join(QDIR, key + ".json")
    if not os.path.exists(pth): return None
    dd = load(pth)
    return dd.get("results", dd) if isinstance(dd, dict) else dd

def wkbucket(recs, qstart, fields):
    b = {}
    for r in recs or []:
        dt = r.get("dt")
        if not dt: continue
        w = (date.fromisoformat(dt) - qstart).days // 7
        if 0 <= w < 13:
            row = {f: r.get(f) for f in fields}
            row["s"] = rnd(row.get("s"))
            b.setdefault(w, []).append(row)
    return b

def row0(key):
    r = q(key); return r[0] if r else {}

# ---- starts: banked / pending / dump-in / lock-up (ESF + PSF) ----
e, p = row0("esf_starts"), row0("psf_starts")
banked_n  = rnd(e.get("banked_n")) + rnd(p.get("banked_n"))
banked_sp = n(e.get("banked_sp")) + n(p.get("banked_sp"))
pending_n = rnd(e.get("pending_n")) + rnd(p.get("pending_n"))
pending_sp= n(e.get("pending_sp")) + n(p.get("pending_sp"))
dumpin_n  = rnd(e.get("dumpin_n")) + rnd(p.get("dumpin_n"))
dumpin_sp = n(e.get("dumpin_sp")) + n(p.get("dumpin_sp"))
lockup_wk = n(e.get("lockup_wk_sp")) + n(p.get("lockup_wk_sp"))
dumpin_wkstart = n(e.get("dumpin_wkstart_sp")) + n(p.get("dumpin_wkstart_sp"))

# --- Carry-in exclusion (Taylor, 2026-09-14) ----------------------------------
# Dump-in is the NET-NEW go-get: what we still had to win after the starts already
# locked up in the prior quarter. Forms created in the opening days of the quarter,
# before the quarterly kickoff, are part of that already-locked-up set and were
# inflating the tile. Stopgap constant until those records carry a Notion tag.
_ci = (goals.get("dumpinCarryInExclusion") or {})
_ci_sp, _ci_n = float(_ci.get("spread") or 0), int(_ci.get("count") or 0)
if _ci_sp:
    dumpin_sp = max(0.0, dumpin_sp - _ci_sp)
    dumpin_n  = max(0, dumpin_n - _ci_n)
    dumpin_wkstart = max(0.0, dumpin_wkstart - _ci_sp)
    warnings.append(f"dump-in excludes {_ci_n} pre-kickoff carry-in form(s) worth ${round(_ci_sp):,} "
                    f"(locked up last quarter — not part of this quarter's go-get)")
max_create = max([d for d in (e.get("max_create"), p.get("max_create")) if d], default=None)
create_blank = rnd(e.get("create_blank")) + rnd(p.get("create_blank"))
esf_total = rnd(e.get("total_rows"))
esf_blank_ratio = (rnd(e.get("create_blank")) / esf_total) if esf_total else 1.0

DUMPIN_GOAL = float((goals.get("company") or {}).get("dumpinSpreadGoal") or 0)
remaining = max(0.0, DUMPIN_GOAL - dumpin_wkstart)
# Weekly lock-up goal. Early in the quarter it paces the remaining gap across the weeks
# left (less a ramp buffer); once that buffer is used up it IS the full remaining gap, which
# is what leadership wants to see in the closing weeks.
wks_to_go = max(0.0, weeks_left)
lock_goal = round(remaining / denom) if DUMPIN_GOAL else None
if DUMPIN_GOAL and denom <= 1.0:
    lock_goal = round(remaining)                      # final stretch: the whole gap, this week
    lock_note = f"${round(remaining):,} remaining to the quarter's dump-in goal · {wks_to_go:.0f} wk{'' if wks_to_go==1 else 's'} left"
elif DUMPIN_GOAL:
    lock_note = f"${round(remaining):,} to dump-in goal / {denom:.1f} wks left (auto-paced)"
else:
    lock_note = None
# A new week starts at $0 and fills in as ESF/PSF are created — never carry last week's
# figure forward (Taylor, 2026-09-14). If the Comtrak feed hasn't reached this week yet the
# tile reads low until it does, and the note below says so.
feed_covers_week = bool(max_create and date.fromisoformat(max_create) >= WK_MON)

# ---- forecast: 13 weeks, IN (ESF/PSF starts) + OUT (placement ends) ----
fin = {}
for key in ("esf_forecast_in", "psf_forecast_in"):
    for r in q(key):
        wk = int(r["wk"]); a = fin.setdefault(wk, [0, 0]); a[0] += n(r.get("insp")); a[1] += rnd(r.get("incount"))
_active_est_spread = float((goals.get("company") or {}).get("unplannedAttritionBase") or 0)
fout = {}
for r in q("placement_forecast_out"):
    fout[int(r["wk"])] = fout.get(int(r["wk"]), 0) + abs(n(r.get("outsp")))
# placement_forecast_out covers CONTRACT ends only (Active Contracts holds no perm rows).
# Perm fees amortize to "Est Last week Spread" and stop the week after, so fold those into
# this quarter's Out bars as well — otherwise the bar and its click-through disagree.
for r in (qopt("perm_rolloff") or []):
    _d = r.get("dt")
    if not _d: continue
    _w = (date.fromisoformat(_d) - QS).days // 7
    if 0 <= _w < 13:
        fout[_w] = fout.get(_w, 0) + abs(n(r.get("s")))
forecast = []
for w in range(13):
    ws = QS + timedelta(days=7 * w)
    isp, ic = fin.get(w, [0, 0])
    forecast.append({"weekStart": ws.isoformat(), "plannedIn": rnd(isp), "inCount": ic, "plannedOut": rnd(fout.get(w, 0))})

# ---- per-week placement detail (click-through) + next-quarter outlook ----
IN_F, OUT_F = ["n", "c", "a", "r", "s", "x"], ["n", "c", "a", "s", "x"]
_in_raw  = (qopt("in_detail_esf_a") or []) + (qopt("in_detail_esf_b") or []) + (qopt("in_detail_psf") or [])
# Perm placements amortize their fee weekly until "Est Last week Spread"; the week AFTER
# that is the roll-off. PSF perm counts on the In side, so it has to count on the Out side
# too or the chart is structurally optimistic. One query feeds both quarters — wkbucket
# keeps only the weeks that fall inside each.
_perm_out = qopt("perm_rolloff") or []
_out_raw = (qopt("out_detail_a") or []) + (qopt("out_detail_b") or []) + _perm_out
have_detail = bool(_in_raw or _out_raw)
if have_detail:
    _ind, _outd = wkbucket(_in_raw, QS, IN_F), wkbucket(_out_raw, QS, OUT_F)
    for _i, _w in enumerate(forecast):
        _w["inDetail"], _w["outDetail"] = _ind.get(_i, []), _outd.get(_i, [])
else:
    warnings.append("detail queries missing — carrying forward the previous file's inDetail/outDetail")
    for _i, _w in enumerate(forecast):
        _prev = (prev.get("scorecard", {}).get("forecast") or [])
        _pw = _prev[_i] if _i < len(_prev) else {}
        _w["inDetail"], _w["outDetail"] = _pw.get("inDetail", []), _pw.get("outDetail", [])

NQS = QE + timedelta(days=1)                       # next quarter starts the day after this one ends
_nin_raw  = (qopt("next_in_detail_esf") or []) + (qopt("next_in_detail_psf") or [])
_nout_raw = (qopt("next_out_detail_a") or []) + (qopt("next_out_detail_b") or []) + _perm_out

# --- Confirmed renewals (Taylor, 2026-09-15) ----------------------------------
# Placements the team has confirmed will renew are NOT attrition. Their Notion end
# date still says they roll off, so strip them here — this keeps them out of BOTH
# the next-quarter Out bars and the rolling lock-up goal. Best fix is updating
# Actual End Date in Notion; clear names from goals.json once that is done.
def _rnkey(s):
    import re as _r
    s = _r.sub(r"\s*/\s*[0-9]+\s*$", "", str(s or ""))     # drop the trailing " / id"
    s = s.replace('"', ' ').replace("'", " ")
    return " ".join(s.split()).lower()
_renew = {_rnkey(x) for x in (goals.get("confirmedRenewals") or [])}
if _renew:
    _keep, _drop = [], []
    for _r0 in _nout_raw:
        (_drop if _rnkey(_r0.get("n")) in _renew else _keep).append(_r0)
    if _drop:
        _nout_raw = _keep
        warnings.append(f"next-qtr roll-off excludes {len(_drop)} confirmed renewal(s) "
                        f"worth ${rnd(sum(abs(n(x.get('s'))) for x in _drop)):,} "
                        f"(held out of the Out bars and the lock-up goal)")
    _hitkeys = {_rnkey(x.get("n")) for x in _drop}
    _un = sorted(x for x in (goals.get("confirmedRenewals") or []) if _rnkey(x) not in _hitkeys)
    if _un:
        warnings.append("confirmedRenewals with no next-qtr roll-off (alias spelling, already "
                        "re-dated in Notion, or a typo): " + ", ".join(_un))
forecast_next = None
if _nin_raw or _nout_raw:
    _nin, _nout = wkbucket(_nin_raw, NQS, IN_F), wkbucket(_nout_raw, NQS, OUT_F)
    _weeks = []
    for w in range(13):
        ws = NQS + timedelta(days=7 * w)
        _i2, _o2 = _nin.get(w, []), _nout.get(w, [])
        _weeks.append({"weekStart": ws.isoformat(),
                       "plannedIn": rnd(sum(x["s"] for x in _i2)), "inCount": len(_i2),
                       "plannedOut": rnd(sum(x["s"] for x in _o2)),
                       "inDetail": _i2, "outDetail": _o2})
    # next quarter's label comes from goals.quarterLabel ("Q3 2026" -> "Q4 2026"), since the
    # fiscal quarters are offset from calendar months and can't be derived from QS.month.
    import re as _re
    _m = _re.match(r"Q([1-4])\s+(\d{4})", str(goals.get("quarterLabel", "")))
    if _m:
        _q, _y = int(_m.group(1)), int(_m.group(2))
        _lbl = f"Q{_q + 1} {_y}" if _q < 4 else f"Q1 {_y + 1}"
    else:
        _lbl = "Next quarter"
    forecast_next = {"label": _lbl, "quarterStart": NQS.isoformat(),
                     "quarterEnd": (NQS + timedelta(days=90)).isoformat(), "weeks": _weeks}

# ---- close ratio: AM Productivity Snapshot (the sanctioned source) ----
# Close Ratio Details carries no usable close date (Status Date is legacy-only), so windowed
# aggregates must NOT come from it. The snapshot's Close Ratio (13wk) is computed from a fresh
# windowed ComTrak close-date pull. Snapshot Date sits 1-2 weeks back by design.
# Taylor, 2026-09-23: the snapshot publishes ONLY "Close Ratio (13wk)" and "Closed Reqs (13wk)"
# — there is no Filled/Washed/Lost breakdown in the table (schema checked). The old code invented
# filled = round(ratio x closed) and decided = closed, then RE-DERIVED ratio = filled/decided.
# That round-trip moved most AMs off their own source number (Hannah 20% -> 16.7%, Brie 85% ->
# 80%) and the invented columns were not even arithmetically possible: for 5 of 9 AMs
# ratio x closed is not a whole number, so Closed Reqs is NOT the ratio's denominator.
# RULE: carry Comtrak's ratio through untouched and never reconstruct a numerator from it.
# Closed Reqs is displayed as its own column and used ONLY as the weight for the company tile.
snap_rows = q("close_ratio_snapshot")
by_am, snap_date = [], None
for r in snap_rows:
    am = r.get("am"); ratio = r.get("ratio"); closed = rnd(r.get("closed"))
    snap_date = snap_date or r.get("snap")
    if not am or am == "Unassigned" or ratio is None or closed <= 0: continue
    by_am.append({"name": am, "ratio": round(float(ratio), 4), "closedReqs": closed,
                  "_rw": float(ratio) * closed,
                  "spread": rnd(r.get("spread")), "delta": rnd(r.get("delta"))})
# ---- latest-closed-week company spread (AM Productivity Snapshot) ----
# Tile value = SUM(Spread) over ALL rows at the latest Snapshot Date — every AM row, not just the
# ones that survive the fill-ratio filter below (that filter drops ratio=None rows and would
# understate the total). Rows are split-adjusted and deduped upstream, so the sum IS the company
# total for that settled week. Snapshot Date is the Monday of the last settled week; a week settles
# 10 days after its Friday end, so the ~1.5-2 week lag behind the calendar is CORRECT, not stale.
# Never recompute this from the raw Spread mirror and never filter by calendar week.
snap_spread = (sum(n(r.get("spread")) for r in snap_rows if r.get("snap") == snap_date)
               if snap_date else None)
snap_n = len([r for r in snap_rows if r.get("snap") == snap_date]) if snap_date else 0

# A duplicate-spelling merge re-weights by Closed Reqs; a single-row AM keeps its exact source
# ratio (x/x round-trips clean).
by_am = merge_rows(by_am, ["closedReqs", "_rw"])
for r in by_am:
    r["ratio"] = round(r["_rw"] / r["closedReqs"], 4) if r["closedReqs"] else 0
    r.pop("_rw", None)
by_am.sort(key=lambda x: -x["ratio"])
# Company tile = Closed-Reqs-weighted mean of the AM ratios. The snapshot carries no company
# row and no filled/decided counts, so this is the only aggregate available; it is an
# approximation of a true pooled ratio and must be labelled as a weighted average, not as
# "filled / decided".
cw = sum(x["ratio"] * x["closedReqs"] for x in by_am)
cdec = sum(x["closedReqs"] for x in by_am)
fill_ratio = {"as_of": TODAY.isoformat(), "window_weeks": 13,
              "basis": "Comtrak Close Ratio (13wk) per AM, carried through verbatim; company = "
                       "Closed-Reqs-weighted mean (snapshot has no filled/decided counts)",
              "snapshot_date": snap_date,
              "company": {"closedReqs": rnd(cdec), "ratio": round(cw / cdec, 4) if cdec else 0},
              "by_am": by_am}

# ---- meetings (13-wk) ----
mtg = [{"name": r["am"], "count": rnd(r.get("c")), "weekly_avg": r1(rnd(r.get("c")) / 13)}
       for r in q("meetings_byam") if r.get("am") and r["am"] != "Unassigned"]
mtg = merge_rows(mtg, ["count"])
for r in mtg: r["weekly_avg"] = r1(r["count"] / 13)
mtg.sort(key=lambda x: -x["weekly_avg"])
meetings = {"quarterly_pace": sum(m["count"] for m in mtg), "by_am": mtg, "lookback_weeks": 13}

# ---- subs (13-wk) ----
# Recruiter Activity is a WINDOWED table — it holds fewer weeks than the 13-week lookback,
# so divide by the weeks actually present, never a hard 13.
_sub_rows = [r for r in q("subs_byrec") if r.get("emp") and r["emp"] != "Unassigned"]
sub_wks = max([rnd(r.get("wks")) for r in _sub_rows] or [0]) or 13
sb = [{"name": r["emp"], "count": rnd(r.get("subs")), "weekly_avg": 0.0} for r in _sub_rows]
sb = merge_rows(sb, ["count"])
for r in sb: r["weekly_avg"] = r1(r["count"] / sub_wks)
sb.sort(key=lambda x: -x["weekly_avg"])
subs = {"weekly_avg": r1(sum(x["weekly_avg"] for x in sb)), "by_recruiter": sb,
        "lookback_weeks": sub_wks}

# ---- last COMPLETE week totals (Q3 goal-tracking tiles) ----
# Taylor, 2026-09-23: the tiles read the last complete Mon-Sun week, never the week in progress
# (which is always a partial and on a Monday would read 0), and never the current day (Contact
# Activity backfills for 1-2 days after the fact). The window advances on its own each Monday.
# Both queries are UNGROUPED single-row aggregates on purpose: the Notion connector splits and
# mis-attributes GROUP BY buckets, and these tiles only need company totals.
LAST_WK = WK_MON - timedelta(days=7)
week_totals = None
week_block_reason = None
_wm, _ws = qopt("week_meetings"), qopt("week_subs")
if _wm or _ws:
    _wmr = (_wm or [{}])[0] if _wm else {}
    _wsr = (_ws or [{}])[0] if _ws else {}
    week_totals = {"week_start": LAST_WK.isoformat(),
                   "week_end": (LAST_WK + timedelta(days=6)).isoformat(),
                   "meetings": rnd(_wmr.get("c")) if _wm else None,
                   "meeting_ams": rnd(_wmr.get("ams")) if _wm else None,
                   "subs": rnd(_wsr.get("subs")) if _ws else None,
                   "sub_recruiters": rnd(_wsr.get("recs")) if _ws else None}
    # The query stamps the window it actually used; if it disagrees with the Monday this run
    # computed, the fixture is from a previous week and the tiles would silently show stale
    # totals under a fresh label. Fail loudly and do not publish a week we cannot vouch for.
    for _k, _rows in (("week_meetings", _wm), ("week_subs", _ws)):
        _w = (_rows or [{}])[0].get("wk") if _rows else None
        if _w and _w != LAST_WK.isoformat():
            warnings.append(f"{_k} fixture covers week {_w}, not {LAST_WK} \u2014 re-run that query; "
                            f"week totals NOT published")
            week_totals = None; week_block_reason = f"{_k} fixture is for week {_w}"
    # Coverage check. A count is only "the week's total" if the feed actually reached the end of
    # that week. Contact Activity backfills for 1-2 days and has stalled for days at a time, and a
    # stalled feed returns a small number, not an error -- which would publish a materially short
    # week under a clean "Week of <date>" label. Require the meetings feed to carry through the
    # Friday and the subs feed to have published that week's row; otherwise carry forward.
    _fri = (LAST_WK + timedelta(days=4)).isoformat()
    _maxd = (_wm or [{}])[0].get("maxd") if _wm else None
    _maxwk = (_ws or [{}])[0].get("maxwk") if _ws else None
    if week_totals and _maxd and _maxd < _fri:
        warnings.append(f"Contact Activity carries no rows past {_maxd} (needs {_fri}, the Friday "
                        f"of week {LAST_WK}) \u2014 feed is behind; week totals NOT published")
        week_totals = None; week_block_reason = f"meetings feed stops at {_maxd}"
    if week_totals and _maxwk and _maxwk < LAST_WK.isoformat():
        warnings.append(f"Recruiter Activity's latest week is {_maxwk}, not {LAST_WK} \u2014 feed is "
                        f"behind; week totals NOT published")
        week_totals = None; week_block_reason = f"subs feed's latest week is {_maxwk}"
else:
    warnings.append("week_meetings/week_subs missing \u2014 last-complete-week tiles CARRIED "
                    "FORWARD from the previous file; re-run those two queries")
    week_block_reason = "week queries did not run"

# ---- hours utilization (Comtrak API table, per-consultant rows only) ----
# The table mixes per-consultant rows with employment-type roll-ups; "Count"=1 isolates the
# per-consultant ones (the query does this). It is WINDOWED, so there is no YTD baseline here —
# the tile compares against the fixed goal from Company Goals (hoursUtilGoal).
merged = {}
for r in q("hours_main_byweek"):
    if r.get("wk"): merged[r["wk"]] = (n(r.get("h")), rnd(r.get("cons")))
_maxc = max([c for _, c in merged.values()] or [0])
settled = {w: v for w, v in merged.items() if v[1] >= 0.5 * _maxc}     # drop unsettled weeks
unsettled = sorted(set(merged) - set(settled))
if unsettled: warnings.append(f"hours: ignoring unsettled week(s) {', '.join(unsettled)} (timesheets still approving)")
_q3 = {w: v for w, v in settled.items() if QS.isoformat() <= w <= TODAY.isoformat()}
_H = sum(v[0] for v in _q3.values()); _C = sum(v[1] for v in _q3.values())
hours_goal = float((goals.get("company") or {}).get("hoursUtilGoal") or 37.5)
by_week = [{"week": w, "avg": r1(settled[w][0] / settled[w][1]) if settled[w][1] else 0,
            "consultants": settled[w][1]} for w in sorted(_q3)]
hours_util = {"current": r1(_H / _C) if _C else 0, "baseline": hours_goal,
              "current_label": "Q3-to-date", "baseline_label": "Goal",
              "current_consultant_weeks": _C, "current_weeks": len(_q3),
              "baseline_weeks": None, "by_week": by_week}

# ---- raffle (building-cohort, preserve + advance) ----
rf = json.loads(json.dumps(prev.get("raffle", {})))
THR = rf.get("threshold", 1250); BATCH = rf.get("batch_size", 15)
acc = rf.get("accrual_start_date") or QS.isoformat()
drawn = list(rf.get("drawn_members", []))
norm = lambda s: str(s or "").strip().lower()
if rf.get("advance_drawing"):
    drawn += [m.get("name") for m in rf.get("current_members", [])]
    rf["current_drawing_no"] = int(rf.get("current_drawing_no", 1)) + 1
    rf["advance_drawing"] = False
drawnset = {norm(x) for x in drawn}
best = {}
for r in q("raffle_candidates"):
    st = r.get("start")
    if not st or st < acc or st > TODAY.isoformat(): continue
    if n(r.get("sp")) < THR: continue
    k = norm(r.get("name"))
    if not k or k in drawnset: continue
    if k not in best or st < best[k]["start"]: best[k] = r
pool = [{"name": r.get("name"), "am": r.get("am") or "Unassigned", "recruiter": r.get("rec") or "", "spread": rnd(r.get("sp"))}
        for r in sorted(best.values(), key=lambda r: r["start"])]
rf["current_members"] = pool[:BATCH]; rf["next_up"] = pool[BATCH:]; rf["drawn_members"] = drawn

# ---- guardrails ----
critical = (banked_n == 0 and pending_n == 0 and dumpin_n == 0)
if not q("meetings_byam") or not q("subs_byrec") or not by_am:
    warnings.append("a lookback table (meetings/subs/fill) returned no rows")

# ---- assemble: start from prev, overwrite computed sections ----
wd = json.loads(json.dumps(prev))
sc = wd["scorecard"]
sc["pending_count"] = pending_n; sc["pending_total_spread"] = rnd(pending_sp)
sc["pending_avg_spread"] = rnd(pending_sp / pending_n) if pending_n else 0
sc["net_new_starts"] = banked_n
sc["avg_start_spread"] = rnd(banked_sp / banked_n) if banked_n else 0

# --- Estimated unplanned attrition (Taylor, 2026-09-15) -----------------------
# Booked roll-off only ever shows placements whose END DATE is already recorded.
# Early terminations are not in the table until someone ends them, so every forward
# week needs an allowance. 2026 actuals (placements whose Actual End Date landed
# before their contracted End Date): Q1 1.09%/wk, Q2 0.66%, Q3 1.00% - blended 0.91%.
# Kept as its own field so the booked Out bars stay exactly equal to their drill-through.
UNP_RATE = float((goals.get("company") or {}).get("unplannedAttritionRate") or 0)
UNP_BASE = float((goals.get("company") or {}).get("unplannedAttritionBase") or 0) or n(_active_est_spread)
_unp_wk = round(UNP_RATE * UNP_BASE) if UNP_RATE else 0
if _unp_wk:
    _n1 = _n2 = 0
    # Inside the final ROLL_WKS weeks of a quarter the remaining weeks are effectively
    # closed — an early termination now barely moves the quarter, and an estimate on those
    # weeks just muddies the close number (Taylor, 2026-09-15). So the allowance applies to
    # this quarter's forward weeks ONLY while we are outside that final stretch.
    _cur_ok = weeks_left > float(goals.get("lockupRollForwardWeeks", 2))
    for _w in forecast:
        if _cur_ok and _w["weekStart"] >= WK_MON.isoformat(): _w["unplannedOut"] = _unp_wk; _n1 += 1
        else: _w["unplannedOut"] = 0
    if forecast_next:
        for _w in forecast_next["weeks"]: _w["unplannedOut"] = _unp_wk; _n2 += 1
    warnings.append(f"unplanned-attrition allowance ${_unp_wk:,}/wk "
                    f"({UNP_RATE*100:.2f}% of ${rnd(UNP_BASE):,}) on {_n1} remaining wk(s) this qtr "
                    f"+ {_n2} wk(s) next qtr — estimate, not booked roll-off"
                    + ("" if _cur_ok else f" · none applied to this quarter's last {weeks_left:.0f} wk(s)"))
sc["forecast"] = forecast
if forecast_next: sc["forecast_next"] = forecast_next
elif "forecast_next" in prev.get("scorecard", {}): sc["forecast_next"] = prev["scorecard"]["forecast_next"]

# --- Rolling lock-up goal (Taylor, 2026-09-14) --------------------------------
# Inside the last ROLL_WKS weeks of a quarter, anything locked up has very little
# chance of walking in before quarter end. So the lock-up target stops chasing
# this quarter's dump-in gap and starts funding the NEXT quarter: cover the
# attrition already booked there, plus a growth step, paced across the rolling
# window (weeks left here + next quarter's weeks) less the ramp buffer.
ROLL_WKS = float(goals.get("lockupRollForwardWeeks", 2))
GROWTH   = float((goals.get("company") or {}).get("quarterGrowthTarget", 30000))
RAMP     = float(goals.get("lockupRampWeeks", 2.5))
_fn = sc.get("forecast_next") or {}
_nqw = _fn.get("weeks") or []
if _nqw and weeks_left <= ROLL_WKS:
    nq_out = sum(abs(w.get("plannedOut") or 0) for w in _nqw)
    # Unplanned attrition is spread we still have to replace even though no placement
    # carries its date yet, so the weekly target has to cover it alongside booked roll-off.
    nq_unp = sum(abs(w.get("unplannedOut") or 0) for w in _nqw)
    nq_out += nq_unp
    # renewals already stripped from _nqw upstream; nq_out is clean here.
    window = int(round(weeks_left)) + len(_nqw)
    rdenom = max(1.0, window - RAMP)
    need   = GROWTH + nq_out
    lock_goal = round(need / rdenom)
    _lbl = _fn.get("label", "next qtr")
    _att = f"${round(nq_out - nq_unp):,} booked" + (f" + ${round(nq_unp):,} unplanned" if nq_unp else "")
    lock_note = (f"${round(need):,} to lock up over the next {window} wks "
                 f"(${round(GROWTH):,} growth + {_lbl} attrition: {_att}) "
                 f"/ {rdenom:.1f} earning wks")
    warnings.append(f"lock-up goal rolled forward to {_lbl}: ${round(need):,} over "
                    f"{window} wks / {rdenom:.1f} earning wks = ${lock_goal:,}/wk")

# --- Lock-up goal hold (Taylor, 2026-09-14) -----------------------------------
# Pin the lock-up goal to the figure already published to the team for the current
# week, so a mid-week rebuild cannot move a number leadership has already seen.
# Once throughDate passes, the computed goal above takes over on its own.
_ov = goals.get("lockupGoalOverride") or {}
if _ov.get("value") and _ov.get("throughDate"):
    if TODAY <= date.fromisoformat(_ov["throughDate"]):
        lock_goal = round(float(_ov["value"]))
        lock_note = _ov.get("note") or f"${lock_goal:,} lock-up target for this week"
        warnings.append(f"lock-up goal HELD at ${lock_goal:,} through {_ov['throughDate']} "
                        f"(published figure); rolling goal resumes after that")
    else:
        warnings.append(f"lockupGoalOverride expired {_ov['throughDate']} — rolling goal now live; "
                        f"remove the key from goals.json")
sc["lockup_count"] = None
if esf_blank_ratio > 0.5:
    warnings.append(f"ESF Create Ts {esf_blank_ratio*100:.0f}% blank — keeping prior dump-in (${sc.get('dumpin_spread',0):,}) and lock-up target")
else:
    sc["dumpin_count"] = dumpin_n; sc["dumpin_spread"] = rnd(dumpin_sp)
    if lock_goal is not None: sc["lockup_spread_goal"] = lock_goal; sc["lockup_target_note"] = lock_note
sc["lockup_spread"] = rnd(lockup_wk)
if not feed_covers_week:
    warnings.append(f"ESF/PSF creates only through {max_create} (< week of {WK_MON}) — lock-up shows ${rnd(lockup_wk):,} so far this week; it fills in as Comtrak loads")
    if sc.get("lockup_target_note"):
        sc["lockup_target_note"] += f" · ESF feed through {max_create}"
if snap_date and snap_spread is not None:
    wd.setdefault("company", {})["weekly_spread"] = round(snap_spread, 2)
    wd["company"]["weekly_spread_week"] = snap_date
    warnings.append(f"company spread ${round(snap_spread):,} = latest settled week {snap_date} "
                    f"({snap_n} AM rows) \u00b7 lags calendar ~1.5-2 wks by design")
else:
    warnings.append("close_ratio_snapshot empty \u2014 company.weekly_spread CARRIED FORWARD "
                    "from the previous file; do not treat the tile as current")
wd["fill_ratio"] = fill_ratio; wd["meetings"] = meetings; wd["subs"] = subs; wd["hours_util"] = hours_util; wd["raffle"] = rf
if week_totals:
    wd["week_totals"] = week_totals
elif wd.get("week_totals"):
    # Carried forward. Stamp it so the tile says so rather than presenting a week-old count as
    # this week's - a carried-forward value is not a value anything checks.
    wd["week_totals"]["carried_forward"] = True
    wd["week_totals"]["carry_reason"] = week_block_reason or "week totals could not be verified"
wd.setdefault("meta", {})["rebuilt_at"] = datetime.now(timezone.utc).isoformat()
wd["meta"]["rebuild_source"] = "comtrak-notion-hybrid-sql"

# ---- report ----
print(f"as-of {TODAY} | week Monday {WK_MON} | ESF max create {max_create} | close ratio snapshot {fill_ratio.get('snapshot_date')} | subs window {sub_wks} wks")
for w in warnings: print("WARN:", w)
def g(o, path):
    for k in path.split("."):
        o = (o or {}).get(k) if isinstance(o, dict) else None
    return o
print("\nDiff (was -> now):")
for f in ["scorecard.net_new_starts","scorecard.avg_start_spread","scorecard.pending_count","scorecard.pending_total_spread",
          "scorecard.dumpin_count","scorecard.dumpin_spread","scorecard.lockup_spread","scorecard.lockup_spread_goal",
          "fill_ratio.company.closedReqs","fill_ratio.company.ratio",
          "week_totals.week_start","week_totals.meetings","week_totals.subs",
          "meetings.quarterly_pace","subs.weekly_avg","hours_util.current","hours_util.baseline","raffle.current_drawing_no"]:
    a, b = g(prev, f), g(wd, f)
    print(f"{'   ' if a==b else ' * '}{f}: {a} -> {b}")
print(f"   raffle current/next: {len(prev.get('raffle',{}).get('current_members',[]))}/{len(prev.get('raffle',{}).get('next_up',[]))} -> {len(rf['current_members'])}/{len(rf['next_up'])}")

if critical: raise SystemExit("CRITICAL: ESF/PSF returned no starts — NOT writing (would blank the dashboard).")
if DRY: print("\n[dry-run] weekly_data.json NOT written."); raise SystemExit(0)
with open(os.path.join(ROOT, "weekly_data.json"), "w") as f:
    json.dump(wd, f, indent=2); f.write("\n")
print("\nWrote weekly_data.json")
