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
max_create = max([d for d in (e.get("max_create"), p.get("max_create")) if d], default=None)
create_blank = rnd(e.get("create_blank")) + rnd(p.get("create_blank"))
esf_total = rnd(e.get("total_rows"))
esf_blank_ratio = (rnd(e.get("create_blank")) / esf_total) if esf_total else 1.0

DUMPIN_GOAL = float((goals.get("company") or {}).get("dumpinSpreadGoal") or 0)
remaining = max(0.0, DUMPIN_GOAL - dumpin_wkstart)
lock_goal = round(remaining / denom) if DUMPIN_GOAL else None
lock_note = (f"${round(remaining):,} to dump-in goal / {denom:.1f} wks left (auto-paced)"
             if DUMPIN_GOAL else None)
lock_covers_week = bool(max_create and date.fromisoformat(max_create) >= WK_MON)

# ---- forecast: 13 weeks, IN (ESF/PSF starts) + OUT (placement ends) ----
fin = {}
for key in ("esf_forecast_in", "psf_forecast_in"):
    for r in q(key):
        wk = int(r["wk"]); a = fin.setdefault(wk, [0, 0]); a[0] += n(r.get("insp")); a[1] += rnd(r.get("incount"))
fout = {}
for r in q("placement_forecast_out"):
    fout[int(r["wk"])] = fout.get(int(r["wk"]), 0) + abs(n(r.get("outsp")))
forecast = []
for w in range(13):
    ws = QS + timedelta(days=7 * w)
    isp, ic = fin.get(w, [0, 0])
    forecast.append({"weekStart": ws.isoformat(), "plannedIn": rnd(isp), "inCount": ic, "plannedOut": rnd(fout.get(w, 0))})

# ---- per-week placement detail (click-through) + next-quarter outlook ----
IN_F, OUT_F = ["n", "c", "a", "r", "s", "x"], ["n", "c", "a", "s", "x"]
_in_raw  = (qopt("in_detail_esf_a") or []) + (qopt("in_detail_esf_b") or []) + (qopt("in_detail_psf") or [])
_out_raw = (qopt("out_detail_a") or []) + (qopt("out_detail_b") or [])
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
_nout_raw = (qopt("next_out_detail_a") or []) + (qopt("next_out_detail_b") or [])
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

# ---- fill ratio: pick freshest status-date column ----
sc_c, adj_c = row0("fill_status_company"), row0("fill_adj_company")
use_adj = bool(adj_c.get("maxd")) and (not sc_c.get("maxd") or adj_c["maxd"] > sc_c["maxd"])
comp = adj_c if use_adj else sc_c
byam_rows = q("fill_adj_byam") if use_adj else q("fill_status_byam")
# Close Ratio = Filled / (Filled + Washed + Lost), to match the Power BI DLT (excludes reqs
# still open in the window). "decided" = the denominator; openings kept for context.
by_am = []
for r in byam_rows:
    o = n(r.get("openings")); f = n(r.get("filled")); w = n(r.get("washed")); l = n(r.get("lost"))
    owner = r.get("owner"); dec = f + w + l
    if not owner or owner == "Unassigned" or dec <= 0: continue
    by_am.append({"name": owner, "filled": rnd(f), "washed": rnd(w), "lost": rnd(l),
                  "openings": rnd(o), "decided": rnd(dec), "ratio": round(f / dec, 4)})
by_am = merge_rows(by_am, ["filled","washed","lost","openings","decided"])
for r in by_am: r["ratio"] = round(r["filled"] / r["decided"], 4) if r["decided"] else 0
by_am.sort(key=lambda x: -x["ratio"])
co, cf, cw, cl = n(comp.get("openings")), n(comp.get("filled")), n(comp.get("washed")), n(comp.get("lost"))
cdec = cf + cw + cl
fill_ratio = {"as_of": TODAY.isoformat(), "window_weeks": 13, "basis": "filled/(filled+washed+lost)",
              "company": {"filled": rnd(cf), "washed": rnd(cw), "lost": rnd(cl), "openings": rnd(co),
                          "decided": rnd(cdec), "ratio": round(cf / cdec, 4) if cdec else 0},
              "by_am": by_am}

# ---- meetings (13-wk) ----
mtg = [{"name": r["am"], "count": rnd(r.get("c")), "weekly_avg": r1(rnd(r.get("c")) / 13)}
       for r in q("meetings_byam") if r.get("am") and r["am"] != "Unassigned"]
mtg = merge_rows(mtg, ["count"])
for r in mtg: r["weekly_avg"] = r1(r["count"] / 13)
mtg.sort(key=lambda x: -x["weekly_avg"])
meetings = {"quarterly_pace": sum(m["count"] for m in mtg), "by_am": mtg, "lookback_weeks": 13}

# ---- subs (13-wk) ----
sb = [{"name": r["emp"], "count": rnd(r.get("subs")), "weekly_avg": r1(n(r.get("subs")) / 13)}
      for r in q("subs_byrec") if r.get("emp") and r["emp"] != "Unassigned"]
sb = merge_rows(sb, ["count"])
for r in sb: r["weekly_avg"] = r1(r["count"] / 13)
sb.sort(key=lambda x: -x["weekly_avg"])
subs = {"weekly_avg": r1(sum(x["weekly_avg"] for x in sb)), "by_recruiter": sb, "lookback_weeks": 13}

# ---- hours utilization (main history table only) ----
# NOTE: the Comtrak "(API)" hours twin stores different units (its Hours values run ~2x the
# main table for the same consultants), so overlaying it doubled the average. Main-only matches
# the source-of-truth figures, so we use it exclusively.
def hmap(key):
    m = {}
    for r in q(key):
        if r.get("wk"): m[r["wk"]] = (n(r.get("h")), rnd(r.get("cons")))
    return m
merged = hmap("hours_main_byweek")
def hu(pred):
    wk = [w for w in merged if pred(w)]
    H = sum(merged[w][0] for w in wk); C = sum(merged[w][1] for w in wk)
    return (r1(H / C) if C else 0, C, len(wk))
q3_avg, q3_cw, q3_wks = hu(lambda w: QS.isoformat() <= w <= TODAY.isoformat())
ytd_avg, _, ytd_wks   = hu(lambda w: w[:4] == "2026")
by_week = [{"week": w, "avg": r1(merged[w][0] / merged[w][1]) if merged[w][1] else 0, "consultants": merged[w][1]}
           for w in sorted(merged) if QS.isoformat() <= w <= TODAY.isoformat()]
hours_util = {"current": q3_avg, "baseline": ytd_avg, "current_label": "Q3-to-date", "baseline_label": "YTD 2026",
              "current_consultant_weeks": q3_cw, "current_weeks": q3_wks, "baseline_weeks": ytd_wks, "by_week": by_week}

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
sc["forecast"] = forecast
if forecast_next: sc["forecast_next"] = forecast_next
elif "forecast_next" in prev.get("scorecard", {}): sc["forecast_next"] = prev["scorecard"]["forecast_next"]
sc["lockup_count"] = None
if esf_blank_ratio > 0.5:
    warnings.append(f"ESF Create Ts {esf_blank_ratio*100:.0f}% blank — keeping prior dump-in (${sc.get('dumpin_spread',0):,}) and lock-up target")
else:
    sc["dumpin_count"] = dumpin_n; sc["dumpin_spread"] = rnd(dumpin_sp)
    if lock_goal is not None: sc["lockup_spread_goal"] = lock_goal; sc["lockup_target_note"] = lock_note
if lock_covers_week:
    sc["lockup_spread"] = rnd(lockup_wk)
else:
    warnings.append(f"ESF/PSF creates only through {max_create} (< week of {WK_MON}) — keeping prior lock-up spread ${sc.get('lockup_spread',0):,} (live-board figure)")
wd["fill_ratio"] = fill_ratio; wd["meetings"] = meetings; wd["subs"] = subs; wd["hours_util"] = hours_util; wd["raffle"] = rf
wd.setdefault("meta", {})["rebuilt_at"] = datetime.now(timezone.utc).isoformat()
wd["meta"]["rebuild_source"] = "comtrak-notion-hybrid-sql"

# ---- report ----
print(f"as-of {TODAY} | week Monday {WK_MON} | ESF max create {max_create} | fill col: {'Adjusted' if use_adj else 'Status'} Date")
for w in warnings: print("WARN:", w)
def g(o, path):
    for k in path.split("."):
        o = (o or {}).get(k) if isinstance(o, dict) else None
    return o
print("\nDiff (was -> now):")
for f in ["scorecard.net_new_starts","scorecard.avg_start_spread","scorecard.pending_count","scorecard.pending_total_spread",
          "scorecard.dumpin_count","scorecard.dumpin_spread","scorecard.lockup_spread","scorecard.lockup_spread_goal",
          "fill_ratio.company.filled","fill_ratio.company.openings","fill_ratio.company.ratio",
          "meetings.quarterly_pace","subs.weekly_avg","hours_util.current","hours_util.baseline","raffle.current_drawing_no"]:
    a, b = g(prev, f), g(wd, f)
    print(f"{'   ' if a==b else ' * '}{f}: {a} -> {b}")
print(f"   raffle current/next: {len(prev.get('raffle',{}).get('current_members',[]))}/{len(prev.get('raffle',{}).get('next_up',[]))} -> {len(rf['current_members'])}/{len(rf['next_up'])}")

if critical: raise SystemExit("CRITICAL: ESF/PSF returned no starts — NOT writing (would blank the dashboard).")
if DRY: print("\n[dry-run] weekly_data.json NOT written."); raise SystemExit(0)
with open(os.path.join(ROOT, "weekly_data.json"), "w") as f:
    json.dump(wd, f, indent=2); f.write("\n")
print("\nWrote weekly_data.json")
