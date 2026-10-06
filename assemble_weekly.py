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
# No fallback quarter: a missing quarterStart/quarterEnd used to default to Q3 2026, which would
# silently build the wrong quarter. Fail loudly instead (Taylor, 2026-09-30 pre-publish audit).
if not goals.get("quarterStart") or not goals.get("quarterEnd"):
    raise SystemExit("goals.json is missing quarterStart/quarterEnd - refusing to guess the quarter.")
QS = date.fromisoformat(goals["quarterStart"])
QE = date.fromisoformat(goals["quarterEnd"])
warnings = []
# Tripwire: the quarter window lives in goals.json but is BAKED into the SQL in
# rebuild_queries.json, and a roll that updates one and not the other builds a quarter's
# tiles from the previous quarter's rows. Read the dates back out of esf_starts rather than
# hardcoding them here, so this check keeps working every quarter without an edit
# (Taylor, 2026-09-28 — the hardcoded version fired a false alarm on the Q3->Q4 roll).
try:
    import re as _re
    _qsql = next(x["sql"] for x in load(os.path.join(ROOT, "scripts", "rebuild_queries.json"))
                 if x.get("key") == "esf_starts")
    _lits = sorted(set(_re.findall(r"\d{4}-\d{2}-\d{2}", _qsql)))
    if _lits and (_lits[0] != QS.isoformat() or _lits[-1] != QE.isoformat()):
        warnings.append(f"goals quarter ({QS}..{QE}) != the window baked into rebuild_queries.json "
                        f"({_lits[0]}..{_lits[-1]}) \u2014 regenerate the queries for the new quarter.")
except Exception as _e:
    warnings.append(f"could not verify rebuild_queries.json's quarter window: {_e}")
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

# --- Confirmed renewals (Taylor, 2026-09-15; widened to the current quarter 2026-09-28) ---
# Placements the team has confirmed will renew are NOT attrition. Their Notion end date
# still says they roll off, so strip them from the Out side. Until the Q3->Q4 roll these
# names only ever landed in the NEXT quarter, so the filter ran on _nout_raw alone; once
# Q4 became the current quarter the same names moved into THIS quarter's Out bars and the
# chart went gross of renewals overnight. The filter now runs on both windows, and the
# current-quarter bar is reduced by exactly what is removed from its drill-through so the
# two keep agreeing. Best fix is still updating Actual End Date in Notion; clear names
# from goals.json once that is done.
def _rnkey(s):
    import re as _r
    s = _r.sub(r"\s*/\s*[0-9]+\s*$", "", str(s or ""))     # drop the trailing " / id"
    s = s.replace('"', ' ').replace("'", " ")
    return " ".join(s.split()).lower()
_renew = {_rnkey(x) for x in (goals.get("confirmedRenewals") or [])}
_renew_hits = set()

def _strip_renewals(rows):
    """Split rows into (kept, dropped-because-confirmed-renewal)."""
    if not _renew: return rows, []
    _keep, _drop = [], []
    for _r0 in rows:
        (_drop if _rnkey(_r0.get("n")) in _renew else _keep).append(_r0)
    _renew_hits.update(_rnkey(x.get("n")) for x in _drop)
    return _keep, _drop

_out_raw, _cur_drop = _strip_renewals(_out_raw)
if _cur_drop:
    # keep the bar equal to its drill-through: take the same money out of both
    for _r0 in _cur_drop:
        _d0 = _r0.get("dt")
        if not _d0: continue
        _w0 = (date.fromisoformat(_d0) - QS).days // 7
        if 0 <= _w0 < len(forecast):
            forecast[_w0]["plannedOut"] = max(0, rnd(forecast[_w0]["plannedOut"]) - rnd(_r0.get("s")))
    warnings.append(f"this-qtr roll-off excludes {len(_cur_drop)} confirmed renewal(s) "
                    f"worth ${rnd(sum(abs(n(x.get('s'))) for x in _cur_drop)):,} "
                    f"(held out of the Out bars and the lock-up goal)")

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

# Confirmed renewals, next-quarter window (the helper and the comment are defined above,
# where the current quarter's Out side is filtered).
_nout_raw, _nxt_drop = _strip_renewals(_nout_raw)
if _nxt_drop:
    warnings.append(f"next-qtr roll-off excludes {len(_nxt_drop)} confirmed renewal(s) "
                    f"worth ${rnd(sum(abs(n(x.get('s'))) for x in _nxt_drop)):,} "
                    f"(held out of the Out bars and the lock-up goal)")
if _renew:
    _un = sorted(x for x in (goals.get("confirmedRenewals") or []) if _rnkey(x) not in _renew_hits)
    if _un:
        warnings.append("confirmedRenewals with no roll-off in either quarter (alias spelling, "
                        "already re-dated in Notion, or a typo): " + ", ".join(_un))
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

# ---- close ratio: Comtrak Raw - Close Ratio Details, windowed on Status Date ----
# Taylor, 2026-10-06: Status Date IS the close date - the day the req moved to Closed - and it is
# now the basis for the 13-week window. This REVERSES the note that used to sit here ("Status
# Date is legacy-only, windowed aggregates must NOT come from it"). That was true once; the field
# has since been backfilled. Coverage by Create year: 2026 307 rows / 0 blank, 2025 545/30,
# 2024 399/11 - complete for any window that matters. The 1,292 blanks are all 2017-2023 history.
# Do not re-point this at Coverage Date.
#
# WHY IT CHANGED: the AM Productivity Snapshot windows on Coverage Date, and every req that
# actually FILLED carries Coverage Date 2038-01-31 - a sentinel, not a date. A 2038 date never
# falls inside a trailing 13 weeks, so the numerator was structurally pinned at zero while the
# denominator collected washes. Company-wide, 2,533 closed reqs carry the sentinel vs 186 with a
# real date, and 1,206 of the sentinel rows have Filled > 0. Hannah Craig read 0% on 3 closed
# reqs when she had 4 fills in the window; Mike Minnillo likewise. Every AM was being scored off
# roughly 7% of their own fills.
#
# Status Date is TEXT in two formats (ISO 2026-08-31 and legacy US 11/05/24) - the query
# normalises both. Window ends the Sunday of the last COMPLETE week so the number holds steady
# Mon-Sun rather than drifting daily.
#
# The snapshot is still read, for Spread / Delta only (Close Ratio Details carries neither), and
# its ratio is kept as a cross-check warning. Never let it drive the tile again.
# Taylor, 2026-09-23: the snapshot publishes ONLY "Close Ratio (13wk)" and "Closed Reqs (13wk)"
# — there is no Filled/Washed/Lost breakdown in the table (schema checked). The old code invented
# filled = round(ratio x closed) and decided = closed, then RE-DERIVED ratio = filled/decided.
# That round-trip moved most AMs off their own source number (Hannah 20% -> 16.7%, Brie 85% ->
# 80%) and the invented columns were not even arithmetically possible: for 5 of 9 AMs
# ratio x closed is not a whole number, so Closed Reqs is NOT the ratio's denominator.
# RULE: carry Comtrak's ratio through untouched and never reconstruct a numerator from it.
# Closed Reqs is displayed as its own column and used ONLY as the weight for the company tile.
snap_rows = q("close_ratio_snapshot")
snap_date = next((r.get("snap") for r in snap_rows if r.get("snap")), None)
_snap_sd = {r.get("am"): r for r in snap_rows if r.get("am")}

_cr_rows = q("close_ratio_statusdate") or []
_cr_total = next((r for r in _cr_rows if r.get("am") == "__TOTAL__"), None)
_cr_ams = [r for r in _cr_rows if r.get("am") and r.get("am") != "__TOTAL__"]
if not _cr_ams:
    raise SystemExit("close_ratio_statusdate returned no AM rows - refusing to publish a blank "
                     "close ratio. Re-pull the fixture.")
# GROUP BY guard: the Notion connector splits and mis-attributes groups, and a per-AM list that
# looks plausible can still be wrong. The __TOTAL__ row is an independent aggregate over the same
# window - if the per-AM rows do not sum to it, the allocation is not trustworthy.
if _cr_total:
    for _k in ("closed", "filled", "lost", "washed"):
        _sum = rnd(sum(n(r.get(_k)) for r in _cr_ams)); _tot = rnd(_cr_total.get(_k))
        if _sum != _tot:
            warnings.append(f"close ratio: per-AM {_k} sums to {_sum} but the independent total "
                            f"reads {_tot} - connector GROUP BY split; ratios NOT trustworthy")
_cr_window_end = (_cr_total or _cr_ams[0]).get("wend")

by_am = []
for r in _cr_ams:
    am = r.get("am")
    f, l, w = n(r.get("filled")), n(r.get("lost")), n(r.get("washed"))
    dec = f + l + w
    if dec <= 0:  # nothing decided in the window - no ratio exists, do not publish a 0
        continue
    _sn = _snap_sd.get(am) or {}
    by_am.append({"name": am, "ratio": round(f / dec, 4), "closedReqs": rnd(r.get("closed")),
                  "filled": rnd(f), "lost": rnd(l), "washed": rnd(w), "decided": rnd(dec),
                  "_rw": f,  # merge_rows re-weights on the true numerator, not ratio x closed
                  "lastClose": r.get("last_close"),
                  "spread": rnd(_sn.get("spread")), "delta": rnd(_sn.get("delta"))})
# Cross-check against the retired basis so a silent regression is visible in the log.
for _r in by_am:
    _sn = _snap_sd.get(_r["name"]) or {}
    if _sn.get("ratio") is not None and abs(float(_sn["ratio"]) - _r["ratio"]) >= 0.25:
        warnings.append(f"close ratio {_r['name']}: now {_r['ratio']:.0%} on {_r['decided']} "
                        f"decided (Status Date) vs {float(_sn['ratio']):.0%} on the retired "
                        f"Coverage-Date snapshot - expected, the snapshot missed filled reqs")
# ---- latest-closed-week company spread (Comtrak Raw - Spread (API)) ----
# Taylor, 2026-09-24: re-pointed from the AM Productivity Snapshot to the raw Spread mirror.
# The snapshot lagged ~2 weeks AND had a five-week hole (nothing for 08-03..08-24), so the tile
# sat a month behind reality. The mirror carries every week and is re-synced daily.
#
# BASIS: "Assignment Type"='Office Manager' rows ONLY. That is the one assignment type with
# complete, unsplit coverage - every placement appears exactly once at Split %=1.00 (plus a
# second row when it has overtime). Account Manager rows undercount (~4-6%: their splits do not
# total 100% on every placement) and Recruiter rows are a third, near-but-not-complete figure.
# Summing across ALL assignment types multiplies the company total ~3x. Never do that.
#
# SETTLE GUARD: a week's rows exist from day one but carry almost no spread until hours post
# (2026-09-21 read $26,274 against a ~$307K norm while already showing 293 consultants), so
# consultant count is NOT a completeness signal - only magnitude is. Publish a week only when it
# has fully ended AND its spread is at least 60% of the median of the four weeks before it.
SPREAD_FLOOR = 0.60
_spw = [r for r in (qopt("spread_week") or []) if r.get("wk")]
_spw.sort(key=lambda r: r["wk"])
spread_row, spread_reason = None, None
for i in range(len(_spw) - 1, -1, -1):
    r = _spw[i]
    wk_end = date.fromisoformat(r["wk"]) + timedelta(days=6)
    if wk_end >= TODAY:
        spread_reason = f"week {r['wk']} has not ended"; continue
    prior = [x["sp"] for x in _spw[max(0, i - 4):i] if n(x.get("sp")) > 0]
    med = sorted(prior)[len(prior) // 2] if prior else 0
    if med and n(r.get("sp")) < SPREAD_FLOOR * med:
        spread_reason = (f"week {r['wk']} at ${round(n(r.get('sp'))):,} is under {int(SPREAD_FLOOR*100)}% "
                         f"of the ${round(med):,} 4-week median - hours still posting")
        continue
    spread_row = r; break

# ---- (retired) AM Productivity Snapshot spread ----
# Tile value = SUM(Spread) over ALL rows at the latest Snapshot Date — every AM row, not just the
# ones that survive the fill-ratio filter below (that filter drops ratio=None rows and would
# understate the total). Rows are split-adjusted and deduped upstream, so the sum IS the company
# total for that settled week. Snapshot Date is the Monday of the last settled week; a week settles
# 10 days after its Friday end, so the ~1.5-2 week lag behind the calendar is CORRECT, not stale.
# Never recompute this from the raw Spread mirror and never filter by calendar week.
# Kept only as a cross-check in the report line; it no longer feeds the tile.
snap_spread = (sum(n(r.get("spread")) for r in snap_rows if r.get("snap") == snap_date)
               if snap_date else None)
snap_n = len([r for r in snap_rows if r.get("snap") == snap_date]) if snap_date else 0

# A duplicate-spelling merge re-weights by Closed Reqs; a single-row AM keeps its exact source
# ratio (x/x round-trips clean).
# Duplicate-spelling merge. Unlike the old snapshot path we hold real counts, so a merge can
# re-derive the ratio honestly from summed filled/decided instead of a weighted average of ratios.
by_am = merge_rows(by_am, ["closedReqs", "filled", "lost", "washed", "decided", "_rw"])
for r in by_am:
    r["ratio"] = round(r["filled"] / r["decided"], 4) if r.get("decided") else 0
    r.pop("_rw", None)
by_am.sort(key=lambda x: -x["ratio"])
# Company tile is now a TRUE pooled ratio - total filled / total decided - not a weighted mean of
# per-AM ratios. Close Ratio Details carries the counts, so the approximation is no longer needed.
cfil = sum(x["filled"] for x in by_am)
cdec = sum(x["decided"] for x in by_am)
fill_ratio = {"as_of": TODAY.isoformat(), "window_weeks": 13,
              "basis": "Comtrak Raw - Close Ratio Details, windowed on Status Date (the date the "
                       "req moved to Closed); ratio = filled / (filled + lost + washed), pooled "
                       "for the company tile",
              "window_end": _cr_window_end,
              "snapshot_date": snap_date,
              "retired_basis": "AM Productivity Snapshot / Coverage Date - filled reqs carry a "
                               "2038-01-31 sentinel date and fell outside every window",
              "company": {"closedReqs": rnd(sum(x["closedReqs"] for x in by_am)),
                          "filled": rnd(cfil), "decided": rnd(cdec),
                          "ratio": round(cfil / cdec, 4) if cdec else 0},
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

# ---- last COMPLETE week totals (goal-tracking tiles) ----
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
    # Magnitude check (Taylor, 2026-09-28). The watermark above only proves the feed REACHED the
    # Friday, not that the Friday is fully loaded - Contact Activity backfills, so a week can sit
    # at a third of its real count with a watermark that passes. Same defence as the spread settle
    # guard: measure against the run rate, not against a timestamp. On the Q3->Q4 roll this week
    # read 53 against a ~91/wk 13-week rate, which would have published a materially short week
    # under a clean "Week of Sep 21" label.
    WEEK_MTG_FLOOR = 0.60
    _rate13 = (meetings.get("quarterly_pace") or 0) / 13.0
    _wc = rnd(_wmr.get("c")) if _wm else None
    if week_totals and _wc is not None and _rate13 and _wc < WEEK_MTG_FLOOR * _rate13:
        warnings.append(f"week {LAST_WK} shows {_wc} meetings, under {int(WEEK_MTG_FLOOR*100)}% of "
                        f"the {_rate13:.0f}/wk 13-week rate \u2014 Contact Activity is still backfilling; "
                        f"week totals NOT published")
        week_totals = None
        week_block_reason = f"week {LAST_WK} meetings ({_wc}) under {int(WEEK_MTG_FLOOR*100)}% of the 13-wk rate"
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
# Hours settle ~10 days after a week ends, so for the first couple of weeks of a quarter the
# QTD window is empty and the tile would read 0.0 hrs - which looks broken rather than honest
# (Taylor, 2026-09-28, Q3->Q4 roll). Fall back to the last 4 settled weeks and say so in the
# label, then switch back to QTD the moment the quarter has a settled week of its own.
_hlabel = ((goals.get("quarterLabel") or "").split(" ")[0] or "Qtr") + "-to-date"
if not _q3 and settled:
    _recent = sorted(settled)[-4:]
    _q3 = {w: settled[w] for w in _recent}
    _hlabel = f"last {len(_recent)} settled wk{'' if len(_recent) == 1 else 's'}"
    warnings.append(f"hours: no settled week inside the quarter yet - tile shows the {_hlabel} "
                    f"({_recent[0]}..{_recent[-1]}) instead of QTD")
_H = sum(v[0] for v in _q3.values()); _C = sum(v[1] for v in _q3.values())
hours_goal = float((goals.get("company") or {}).get("hoursUtilGoal") or 37.5)
by_week = [{"week": w, "avg": r1(settled[w][0] / settled[w][1]) if settled[w][1] else 0,
            "consultants": settled[w][1]} for w in sorted(_q3)]
hours_util = {"current": r1(_H / _C) if _C else 0, "baseline": hours_goal,
              "current_label": _hlabel, "baseline_label": "Goal",
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

# ---- lock-up drill-through (Josh, 2026-10-05) --------------------------------
# Every ESF/PSF created in the CURRENT Mon-Sun week, with company / role / AM / recruiter /
# spread and the Comtrak "Onboard Status" stage. One list, not two: the lock-up number already
# counts forms at every stage, so splitting "accepted" from "still onboarding" into separate
# totals would invite reading the tile as only the first one. The stage column is what lets a
# reader tell them apart, and the rows sum to the tile by construction (same window, same
# Status NOT LIKE '%Cancel%' filter as lockup_wk_sp).
_lu_rows = []
for _r0 in (q("lockup_detail") or []):
    if not _r0.get("n"):
        continue
    _lu_rows.append({"dt": _r0.get("dt"), "n": _r0.get("n"), "c": _r0.get("c"),
                     "j": _r0.get("j"), "a": _r0.get("a"), "r": _r0.get("r"),
                     "s": rnd(_r0.get("s")), "ob": _r0.get("ob"),
                     "x": _r0.get("x"), "sd": _r0.get("sd"), "t": _r0.get("t")})
_lu_rows.sort(key=lambda z: -(z["s"] or 0))
sc["lockup_detail"] = _lu_rows

# ---- offers accepted/extended this week with no ESF or PSF yet (Josh, 2026-10-06) -----------
# Bottom half of the lock-up modal: what the team won this week that the lock-up number cannot
# see because the start form has not been written.
#
# MATCHING: a candidate counts as "already has a form" on ANY of four keys, because no single one
# is sufficient. PSF carries Candidate Id, which equals the submittal's candidate_id exactly.
# ESF does NOT - its Contractor Id is an employee id minted at ESF creation (2189 vs 225617) - so
# ESF matches on Jobdefinition Id, which does equal the submittal's req. Req id alone produces
# false positives when a role is duplicated across reqs (Brad Miller was offered on Home Depot
# 3296 and 2949 on the same day; the ESF landed on 2949), so name is matched too. Name alone
# fails on spelling drift (submittal "Kimberly Troxler Black" vs ESF "Kimberly Troxer Black"),
# which is why req id stays in. The four together resolved all 58 offers in an 8-week backtest.
#
# Internal Innovien reqs are filtered at source (Customer LIKE 'Innovien%') - recruiting for our
# own seats runs through the same submittal pipeline and would otherwise dominate a short list
# with $0-$38 "spread" rows.
#
# EXPECT THIS TO BE EMPTY MOST WEEKS. Offers convert to an ESF the same day almost without
# exception; across 8 weeks exactly one genuine straggler existed. An empty bottom section is
# the normal, healthy state - it does not mean the feed is broken.
_off_rows = []
for _r0 in (q("lockup_offers") or []):
    if not _r0.get("n"):
        continue
    _off_rows.append({"dt": _r0.get("dt"), "n": _r0.get("n"), "c": _r0.get("c"),
                      "j": _r0.get("j"), "r": _r0.get("r"), "s": rnd(_r0.get("s")),
                      "st": _r0.get("st"), "req": _r0.get("reqid")})
_off_rows.sort(key=lambda z: -(z["s"] or 0))
sc["lockup_offers"] = _off_rows
# Bar-vs-drill-through guard, same bar we hold the In/Out modals to. A mismatch means the
# fixture and the aggregate were pulled at different times - publish the warning, not a
# silently wrong modal.
_lu_det = rnd(sum((z["s"] or 0) for z in _lu_rows))
if _lu_rows and _lu_det != rnd(lockup_wk):
    warnings.append(f"lock-up drill-through sums to ${_lu_det:,} but the tile reads "
                    f"${rnd(lockup_wk):,} - re-pull lockup_detail; modal and tile disagree")

if not feed_covers_week:
    warnings.append(f"ESF/PSF creates only through {max_create} (< week of {WK_MON}) — lock-up shows ${rnd(lockup_wk):,} so far this week; it fills in as Comtrak loads")
    if sc.get("lockup_target_note"):
        sc["lockup_target_note"] += f" · ESF feed through {max_create}"
if spread_row:
    wd.setdefault("company", {})["weekly_spread"] = round(n(spread_row.get("sp")), 2)
    wd["company"]["weekly_spread_week"] = spread_row["wk"]
    wd["company"]["weekly_spread_straight_time"] = round(n(spread_row.get("sp_st")), 2)
    wd["company"]["weekly_spread_consultants"] = rnd(spread_row.get("cons"))
    wd["company"]["weekly_spread_basis"] = ("Comtrak Raw \u2013 Spread (API), Office Manager rows "
                                            "(every placement once at 100%), all hours types")
    _ot = n(spread_row.get("sp")) - n(spread_row.get("sp_st"))
    warnings.append(f"company spread ${round(n(spread_row.get('sp'))):,} = week of {spread_row['wk']} "
                    f"({rnd(spread_row.get('cons'))} consultants, ${round(_ot):,} of it overtime) "
                    f"\u00b7 straight time alone ${round(n(spread_row.get('sp_st'))):,}")
    if snap_spread is not None and snap_date:
        warnings.append(f"cross-check: AM Productivity Snapshot still reads {snap_date} at "
                        f"${round(snap_spread):,} ({snap_n} AM rows) \u2014 retired as the tile source")
else:
    warnings.append(f"no publishable spread week ({spread_reason or 'spread_week returned no rows'}) "
                    f"\u2014 company.weekly_spread CARRIED FORWARD; do not treat the tile as current")
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
print(f"as-of {TODAY} | week Monday {WK_MON} | ESF max create {max_create} | close ratio thru {fill_ratio.get('window_end')} (Status Date) | subs window {sub_wks} wks | spread week {spread_row['wk'] if spread_row else 'NONE'}")
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
          "company.weekly_spread","company.weekly_spread_week",
          "meetings.quarterly_pace","subs.weekly_avg","hours_util.current","hours_util.baseline","raffle.current_drawing_no"]:
    a, b = g(prev, f), g(wd, f)
    print(f"{'   ' if a==b else ' * '}{f}: {a} -> {b}")
print(f"   raffle current/next: {len(prev.get('raffle',{}).get('current_members',[]))}/{len(prev.get('raffle',{}).get('next_up',[]))} -> {len(rf['current_members'])}/{len(rf['next_up'])}")

if critical: raise SystemExit("CRITICAL: ESF/PSF returned no starts — NOT writing (would blank the dashboard).")
if DRY: print("\n[dry-run] weekly_data.json NOT written."); raise SystemExit(0)
with open(os.path.join(ROOT, "weekly_data.json"), "w") as f:
    json.dump(wd, f, indent=2); f.write("\n")
print("\nWrote weekly_data.json")
