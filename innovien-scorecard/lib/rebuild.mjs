// lib/rebuild.mjs
// Hybrid Comtrak-Notion rebuild of weekly_data.json.
//
// Reads the "Comtrak Raw" Notion tables and recomputes the history-based scorecard
// sections, mirroring build_scorecard_from_pbi.py (the Excel generator). It is a
// HYBRID: history comes from the full-history main tables; for Hours Utilization and
// Spread it overlays the fresher "(API)" twin for any weeks that twin covers.
//
// Figures these timesheet-lagged tables cannot reproduce (the live-board
// company.weekly_spread and the current-week lock-up total) are CARRIED FORWARD from
// the previous weekly_data.json. Raffle state is preserved and advanced via its flag.
//
// Exported: rebuildWeekly({ notion, prevWeekly, goals, today }) -> { wd, warnings, stats, critical }

import { queryAll, P } from "./notion.js";

// Comtrak Raw database PAGE ids (main = full history). Overridable via env.
export const RAW = {
  esf:        process.env.RAW_ESF        || "5ffd72a0563a4d049bf97a32e6a97336",
  psf:        process.env.RAW_PSF        || "dab00b24c1f9418eb7f3238bb24d2e60",
  placement:  process.env.RAW_PLACEMENT  || "061a34b58ce2420ea211941f80cebc0c",
  closeRatio: process.env.RAW_CLOSERATIO || "2d760a73fdde4c48acbc078c04db901d",
  contact:    process.env.RAW_CONTACT    || "5940657d89ec40c6859841f7b6138260",
  recruiter:  process.env.RAW_RECRUITER  || "460a6250692041f2944a350bcd888513",
  hours:      process.env.RAW_HOURS      || "0f3e1670f7664e54b5c51bdec86e0049",
  spread:     process.env.RAW_SPREAD     || "7578dc4396f04d3b98fe1d310a3ee81e",
  // fresher "(API)" twins (same value columns as their main table) for the overlay
  hoursApi:   process.env.RAW_HOURS_API  || "891b49fee6704226a1ffa818cd4f3ccb",
  spreadApi:  process.env.RAW_SPREAD_API || "82d53f0b049a42418793d12a990f1a25",
};

const DAY = 86400000;
const isoOf = (ms) => new Date(ms).toISOString().slice(0, 10);

function parseDate(v) {
  if (v == null) return null;
  if (v instanceof Date) return Date.UTC(v.getUTCFullYear(), v.getUTCMonth(), v.getUTCDate());
  const s = String(v).trim();
  if (!s) return null;
  const iso = s.split("T")[0].split(" ")[0];
  let m;
  if ((m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(iso))) return Date.UTC(+m[1], +m[2] - 1, +m[3]);
  if ((m = /^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/.exec(s))) {
    let yr = +m[3]; if (yr < 100) yr += 2000;
    return Date.UTC(yr, +m[1] - 1, +m[2]);
  }
  const t = Date.parse(s);
  if (!isNaN(t)) { const dt = new Date(t); return Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth(), dt.getUTCDate()); }
  return null;
}
function toNum(x) { if (x == null) return 0; const n = parseFloat(String(x).replace(/[$,]/g, "")); return isNaN(n) ? 0 : n; }
const r1 = (x) => Math.round(x * 10) / 10;
const commas = (n) => Math.round(n).toLocaleString("en-US");

export async function rebuildWeekly({ notion, prevWeekly, goals, today }) {
  const warnings = [];
  const TODAY = today ? parseDate(today) : parseDate(new Date());
  const QS = parseDate(goals.quarterStart || "2026-06-29");
  const QE = parseDate(goals.quarterEnd || "2026-09-27");
  const WK_MON = TODAY - ((new Date(TODAY).getUTCDay() + 6) % 7) * DAY; // Monday of TODAY's week
  const WK_SUN = WK_MON + 6 * DAY;
  const W13 = TODAY - 91 * DAY;

  // ---- pull tables (server-side date filters where the table is large) --------------
  const [esfP, psfP, placeP, crP, recP, contactP, hoursMainP, hoursApiP, spreadMainP, spreadApiP] =
    await Promise.all([
      queryAll(notion, RAW.esf),
      queryAll(notion, RAW.psf),
      queryAll(notion, RAW.placement),
      queryAll(notion, RAW.closeRatio),
      queryAll(notion, RAW.recruiter),
      queryAll(notion, RAW.contact, { property: "Date", date: { on_or_after: isoOf(W13) } }),
      queryAll(notion, RAW.hours, { property: "Week", date: { on_or_after: "2026-01-01" } }),
      queryAll(notion, RAW.hoursApi),
      queryAll(notion, RAW.spread, { property: "Week", date: { on_or_after: isoOf(WK_MON - 42 * DAY) } }),
      queryAll(notion, RAW.spreadApi),
    ]);

  const critical = esfP.length === 0 || contactP.length === 0 || crP.length === 0 || recP.length === 0;

  // ---- ESF + PSF start-form records -------------------------------------------------
  const recs = [];
  for (const pg of esfP) {
    recs.push({
      src: "ESF", status: P.text(pg, "Status") || "",
      start: parseDate(P.date(pg, "Start Date")), exp: parseDate(P.date(pg, "Expected Start Date")),
      sp: toNum(P.num(pg, "Est Weekly Spread")), create: parseDate(P.date(pg, "Create Ts")),
      name: P.text(pg, "Contractor"), am: P.text(pg, "AM"), recruiter: P.text(pg, "Recruiter"),
    });
  }
  for (const pg of psfP) {
    const st = parseDate(P.date(pg, "Start Date"));
    recs.push({
      src: "PSF", status: P.text(pg, "Status") || "", start: st, exp: st,
      sp: toNum(P.num(pg, "Est Weekly Spread")), create: parseDate(P.date(pg, "Create Ts")),
      name: P.text(pg, "Candidate"), am: P.text(pg, "AM"), recruiter: P.text(pg, "Recruiter"),
    });
  }
  const isDone = (s) => /complete/i.test(s);
  const isDead = (s) => /cancel/i.test(s);
  const agg = (rows) => { const tot = Math.round(rows.reduce((a, x) => a + x.sp, 0)); return { n: rows.length, tot, avg: rows.length ? Math.round(tot / rows.length) : 0 }; };

  const pend = agg(recs.filter((x) => !isDone(x.status) && !isDead(x.status) && x.start != null && x.start >= TODAY && x.start <= QE));
  const bk = agg(recs.filter((x) => isDone(x.status) && x.start != null && x.start >= QS && x.start <= TODAY));
  const di = agg(recs.filter((x) => !isDead(x.status) && x.create != null && x.create >= QS && x.start != null && x.start <= QE));

  // ESF Create-Ts blank guardrail (a bad export blanks the column and collapses dump-in)
  const esfCreates = esfP.map((pg) => parseDate(P.date(pg, "Create Ts"))).filter((v) => v != null);
  const esfBlankRatio = esfP.length ? 1 - esfCreates.length / esfP.length : 1;
  const maxCreate = esfCreates.length ? Math.max(...esfCreates) : 0;

  // ---- Weekly Lock-Up (ESF/PSF created this week) + auto-paced target ---------------
  const lockTotal = Math.round(recs.filter((x) => x.create != null && x.create >= WK_MON && x.create <= WK_SUN && !isDead(x.status)).reduce((a, x) => a + x.sp, 0));
  const lockCoversWeek = maxCreate >= WK_MON;
  const DUMPIN_GOAL = Number((goals.company || {}).dumpinSpreadGoal || 0);
  const RAMP = 2.5;
  const dumpinWkStart = recs.filter((x) => !isDead(x.status) && x.create != null && x.create >= QS && x.create < WK_MON && x.start != null && x.start <= QE).reduce((a, x) => a + x.sp, 0);
  const weeksLeft = ((QE - WK_MON) / DAY + 1) / 7;
  const denom = Math.max(1, weeksLeft - RAMP);
  const remaining = Math.max(0, DUMPIN_GOAL - dumpinWkStart);
  const lockGoal = DUMPIN_GOAL ? Math.round(remaining / denom) : null;
  const lockNote = DUMPIN_GOAL ? `$${commas(remaining)} to dump-in goal / ${denom.toFixed(1)} wks left (auto-paced)` : null;

  // ---- Forecast: 13 quarter weeks. IN = ESF/PSF starts; OUT = Placement end dates ---
  const placements = placeP.map((pg) => ({ end: parseDate(P.date(pg, "Actual End Date")) ?? parseDate(P.date(pg, "End Date")), sp: toNum(P.num(pg, "Est Spread")) }));
  const forecast = [];
  for (let w = 1; w <= 13; w++) {
    const wmon = QS + 7 * (w - 1) * DAY, wend = wmon + 7 * DAY;
    const rin = recs.filter((x) => x.start != null && x.start >= wmon && x.start < wend && !isDead(x.status));
    const outSp = Math.round(placements.filter((p) => p.end != null && p.end >= wmon && p.end < wend).reduce((a, p) => a + Math.abs(p.sp), 0));
    forecast.push({ weekStart: isoOf(wmon), plannedIn: Math.round(rin.reduce((a, x) => a + x.sp, 0)), inCount: rin.length, plannedOut: outSp });
  }

  // ---- Fill Ratio (Close Ratio Details) --------------------------------------------
  // Close Ratio = Filled / (Filled + Washed + Lost), to match the Power BI DLT (excludes reqs
  // still open in the window). "decided" = the denominator; openings kept for context.
  const crRows = crP.map((pg) => ({ owner: P.text(pg, "Owner") || "Unassigned", open: toNum(P.num(pg, "Openings")), fill: toNum(P.num(pg, "Filled")), wash: toNum(P.num(pg, "Washed")), lost: toNum(P.num(pg, "Lost")), sd: parseDate(P.text(pg, "Status Date")), asd: parseDate(P.text(pg, "Adjusted Status Date")) }));
  const maxCol = (k) => crRows.reduce((m, r) => (r[k] != null && r[k] > m ? r[k] : m), 0);
  const dcol = maxCol("asd") > maxCol("sd") ? "asd" : "sd"; // pick the freshest status-date column
  const frWindow = (lo) => {
    const op = {}, fl = {}, wa = {}, ls = {}; let To = 0, Tf = 0, Tw = 0, Tl = 0;
    for (const r of crRows) { const d = r[dcol]; if (d == null || d < lo || d > TODAY) continue;
      op[r.owner] = (op[r.owner] || 0) + r.open; fl[r.owner] = (fl[r.owner] || 0) + r.fill;
      wa[r.owner] = (wa[r.owner] || 0) + r.wash; ls[r.owner] = (ls[r.owner] || 0) + r.lost;
      To += r.open; Tf += r.fill; Tw += r.wash; Tl += r.lost; }
    return { op, fl, wa, ls, To, Tf, Tw, Tl };
  };
  const q = frWindow(QS), t13 = frWindow(W13);
  const dec13 = (a) => (t13.fl[a] || 0) + (t13.wa[a] || 0) + (t13.ls[a] || 0);
  const byAm = Object.keys(t13.op).filter((a) => dec13(a) > 0 && a !== "Unassigned")
    .map((a) => ({ name: a, filled: Math.round(t13.fl[a]), washed: Math.round(t13.wa[a] || 0), lost: Math.round(t13.ls[a] || 0), openings: Math.round(t13.op[a]), decided: Math.round(dec13(a)), ratio: Math.round((t13.fl[a] / dec13(a)) * 1e4) / 1e4 }))
    .sort((x, y) => y.ratio - x.ratio);
  const qDec = q.Tf + q.Tw + q.Tl;
  const fillRatio = { as_of: isoOf(TODAY), window_weeks: 13, basis: "filled/(filled+washed+lost)", company: { filled: Math.round(q.Tf), washed: Math.round(q.Tw), lost: Math.round(q.Tl), openings: Math.round(q.To), decided: Math.round(qDec), ratio: qDec ? Math.round((q.Tf / qDec) * 1e4) / 1e4 : 0 }, by_am: byAm };

  // ---- Meetings (Contact Activity, 13-wk) ------------------------------------------
  const MEET = new Set(["meeting (v)", "first meeting (v)", "req meeting (v)", "lunch", "rbm", "meeting", "first meeting", "swag drop", "bagel drop", "req meeting", "lunch (v)", "intro meeting (v)", "intro meeting", "swag/bagel/treat drop", "intro meeting (v) *", "meeting (v) *", "intro meeting *", "meeting *", "req meeting *", "req meeting (v) *", "rbm *", "lunch *", "swag/bagel/treat drop *"]);
  const mtg13 = {};
  for (const pg of contactP) {
    const a = (P.text(pg, "Activity") || "").trim().toLowerCase();
    if (!MEET.has(a)) continue;
    const d = parseDate(P.date(pg, "Date")), am = P.text(pg, "AM") || P.text(pg, "Owner") || "Unassigned";
    if (d != null && d >= W13 && d <= TODAY) mtg13[am] = (mtg13[am] || 0) + 1;
  }
  const mtgByAm = Object.entries(mtg13).filter(([am]) => am && am !== "Unassigned")
    .map(([am, c]) => ({ name: am, count: c, weekly_avg: r1(c / 13) })).sort((a, b) => b.weekly_avg - a.weekly_avg);
  const meetings = { quarterly_pace: mtgByAm.reduce((a, m) => a + m.count, 0), by_am: mtgByAm, lookback_weeks: 13 };

  // ---- Subs (Recruiter Activity main, 13-wk) ---------------------------------------
  const sub13 = {};
  for (const pg of recP) {
    const d = parseDate(P.date(pg, "Week")); if (d == null || d < W13 || d > TODAY) continue;
    const emp = P.text(pg, "Employee") || P.text(pg, "Record Id") || "Unassigned";
    sub13[emp] = (sub13[emp] || 0) + toNum(P.num(pg, "Submitted To Client"));
  }
  const subByRec = Object.entries(sub13).filter(([e]) => e && e !== "Unassigned")
    .map(([e, c]) => ({ name: e, count: Math.round(c), weekly_avg: r1(c / 13) })).sort((a, b) => b.weekly_avg - a.weekly_avg);
  const subs = { weekly_avg: r1(subByRec.reduce((a, r) => a + r.weekly_avg, 0)), by_recruiter: subByRec, lookback_weeks: 13 };

  // ---- Hours Utilization (main history + "(API)" overlay for the weeks it covers) ---
  const hoursRows = (pages) => pages.filter((pg) => P.text(pg, "Status") === "Approved" && P.text(pg, "is_detail") === "N")
    .map((pg) => ({ c: (P.text(pg, "Contractor") || "").trim(), w: parseDate(P.date(pg, "Week")), h: toNum(P.num(pg, "Hours")) }))
    .filter((r) => r.c && r.w != null);
  // NOTE: the Comtrak "(API)" hours twin stores different units (Hours ~2x the main table for the
  // same consultants), so overlaying it doubled the average. Use the main history table only.
  const mergedH = hoursRows(hoursMainP);
  const cw = {};
  for (const r of mergedH) { const k = r.c + "|" + r.w; cw[k] = (cw[k] || 0) + r.h; }
  const cwe = Object.entries(cw).map(([k, h]) => ({ w: +k.split("|")[1], h }));
  const huavg = (pred) => { const v = cwe.filter((e) => pred(e.w)); const wks = new Set(v.map((e) => e.w)).size; return { avg: v.length ? r1(v.reduce((a, e) => a + e.h, 0) / v.length) : 0, n: v.length, wks }; };
  const ytd = huavg((w) => new Date(w).getUTCFullYear() === 2026);
  const q3 = huavg((w) => w >= QS && w <= TODAY);
  const byWeek = [...new Set(cwe.filter((e) => e.w >= QS && e.w <= TODAY).map((e) => e.w))].sort((a, b) => a - b)
    .map((wk) => { const v = cwe.filter((e) => e.w === wk); return { week: isoOf(wk), avg: r1(v.reduce((a, e) => a + e.h, 0) / v.length), consultants: v.length }; });
  const hoursUtil = { current: q3.avg, baseline: ytd.avg, current_label: "Q3-to-date", baseline_label: "YTD 2026", current_consultant_weeks: q3.n, current_weeks: q3.wks, baseline_weeks: ytd.wks, by_week: byWeek };

  // ---- $1,250 Raffle (building-cohort, preserve + advance) --------------------------
  const rf = JSON.parse(JSON.stringify(prevWeekly.raffle || {}));
  const THR = rf.threshold ?? 1250, BATCH = rf.batch_size ?? 15;
  const acc = parseDate(rf.accrual_start_date) ?? QS;
  let drawn = Array.isArray(rf.drawn_members) ? rf.drawn_members.slice() : [];
  const norm = (n) => String(n || "").trim().toLowerCase();
  if (rf.advance_drawing) { drawn = drawn.concat((rf.current_members || []).map((m) => m.name)); rf.current_drawing_no = (rf.current_drawing_no || 1) + 1; rf.advance_drawing = false; }
  const drawnset = new Set(drawn.map(norm));
  const best = {};
  for (const x of recs) {
    if (!isDone(x.status) || (x.sp || 0) < THR) continue;
    if (x.start == null || x.start < acc || x.start > TODAY) continue;
    const k = norm(x.name); if (!k || drawnset.has(k)) continue;
    if (!(k in best) || x.start < best[k].start) best[k] = x;
  }
  const pool = Object.values(best).sort((a, b) => a.start - b.start)
    .map((x) => ({ name: x.name, am: x.am || "Unassigned", recruiter: x.recruiter || "", spread: Math.round(x.sp || 0) }));
  rf.current_members = pool.slice(0, BATCH); rf.next_up = pool.slice(BATCH); rf.drawn_members = drawn;

  // ---- assemble: start from prev, overwrite only the computed sections --------------
  const wd = JSON.parse(JSON.stringify(prevWeekly));
  const sc = wd.scorecard;
  sc.pending_count = pend.n; sc.pending_total_spread = pend.tot; sc.pending_avg_spread = pend.avg;
  sc.net_new_starts = bk.n; sc.avg_start_spread = bk.avg;
  sc.forecast = forecast;
  sc.lockup_count = null;

  if (esfBlankRatio > 0.5) {
    warnings.push(`ESF Create Ts ${(esfBlankRatio * 100).toFixed(0)}% blank — keeping prior dump-in ($${commas(sc.dumpin_spread || 0)}) and lock-up target`);
  } else {
    sc.dumpin_count = di.n; sc.dumpin_spread = di.tot;
    if (lockGoal != null) { sc.lockup_spread_goal = lockGoal; sc.lockup_target_note = lockNote; }
  }
  if (lockCoversWeek) sc.lockup_spread = lockTotal;
  else warnings.push(`ESF/PSF creates only through ${maxCreate ? isoOf(maxCreate) : "n/a"} (< week of ${isoOf(WK_MON)}) — keeping prior lock-up spread $${commas(sc.lockup_spread || 0)} (live-board figure)`);

  wd.fill_ratio = fillRatio; wd.meetings = meetings; wd.subs = subs; wd.hours_util = hoursUtil; wd.raffle = rf;
  // company.weekly_spread is a live-board figure not in these tables — left untouched (carried forward).

  wd.meta = wd.meta || {};
  wd.meta.rebuilt_at = new Date().toISOString();
  wd.meta.rebuild_source = "comtrak-notion-hybrid";

  const stats = {
    rows: { esf: esfP.length, psf: psfP.length, placement: placeP.length, closeRatio: crP.length, contact13: contactP.length, recruiter: recP.length, hoursMain: hoursMainP.length, hoursApi: hoursApiP.length, spreadMain: spreadMainP.length, spreadApi: spreadApiP.length },
    headline: { pending_count: pend.n, pending_total_spread: pend.tot, net_new_starts: bk.n, avg_start_spread: bk.avg, dumpin_spread: sc.dumpin_spread, lockup_spread: sc.lockup_spread, lockup_spread_goal: sc.lockup_spread_goal, fill_ratio_qtd: fillRatio.company.ratio, meetings_qpace: meetings.quarterly_pace, subs_weekly_avg: subs.weekly_avg, hours_current: hoursUtil.current, raffle_building: rf.current_members.length, raffle_next_up: rf.next_up.length },
    fillDateColumn: dcol, esfMaxCreate: maxCreate ? isoOf(maxCreate) : null, weekMonday: isoOf(WK_MON),
  };
  return { wd, warnings, stats, critical };
}
