// Vercel serverless function: GET /api/scorecard
// Pulls live rows from the 6 Comtrak-fed Notion databases, maps them, and returns
// the computed Weekly Stretch Scorecard JSON. Goals come from goals.json (editable).
import { readFile } from "node:fs/promises";
import { getClient, DB, queryAll, P } from "../lib/notion.js";
import { buildScorecard } from "../lib/metrics.js";

let cache = { at: 0, payload: null };
const TTL_MS = 5 * 60 * 1000; // 5 min server cache

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
  const refresh = req.query?.refresh === "1";
  try {
    if (!refresh && cache.payload && Date.now() - cache.at < TTL_MS) {
      return res.status(200).json({ ...cache.payload, cached: true });
    }
    const goals = JSON.parse(await readFile(new URL("../goals.json", import.meta.url)));
    // Canonical weekly data file (single source of truth shared across dashboards).
    // Drives the Stretch Scorecard tab; any null field falls back to live Notion.
    let weekly = null;
    try { weekly = JSON.parse(await readFile(new URL("../weekly_data.json", import.meta.url))); } catch {}
    const notion = getClient();

    const [ac, rd, am, or_, pe, inx, esf, psf, goalRows] = await Promise.all([
      queryAll(notion, DB.activeContracts),
      queryAll(notion, DB.recruiterDaily),
      queryAll(notion, DB.amWeekly),
      queryAll(notion, DB.openReqs),
      queryAll(notion, DB.placementEvents),
      queryAll(notion, DB.innovienNext),
      queryAll(notion, DB.esfPipeline),
      queryAll(notion, DB.psfPipeline),
      queryAll(notion, DB.companyGoals),
    ]);

    // Overlay live goals from the Company Goals DB (Metric Key -> Value) onto the JSON fallback.
    const KEYMAP = {
      weekly_spread: ["company","weeklySpreadGoal"], qtr_starts: ["company","qtrStartsGoal"],
      avg_start_spread: ["company","avgStartGoal"], pending_avg_spread: ["company","pendingAvgGoal"],
      weekly_lockup_count: ["company","weeklyLockupCountGoal"], weekly_lockup_spread: ["company","weeklyLockupSpreadGoal"],
      weekly_subs: ["company","weeklySubGoal"], qtrly_meetings: ["company","qtrlyMeetingGoal"],
      fill_ratio: ["company","fillRatioGoal"], redeployed: ["company","redeployedGoal"], year_spread: ["company","yearSpreadGoal"],
      dumpin_spread: ["company","dumpinSpreadGoal"],
    };
    const qLabel = goals.quarterLabel;
    let goalsApplied = 0;
    for (const row of goalRows) {
      const active = row.properties?.Active?.checkbox;
      const key = P.text(row, "Metric Key");
      const val = P.num(row, "Value");
      const period = P.text(row, "Period");
      const scope = P.sel(row, "Scope");
      if (!active || key == null || val == null) continue;
      if (period && period !== qLabel && !/^\d{4}$/.test(period)) continue; // match quarter (or annual yyyy)
      if (KEYMAP[key]) { goals[KEYMAP[key][0]][KEYMAP[key][1]] = val; goalsApplied++; }
      else if (key === "per_am_weekly_meetings" && scope === "Per AM") { goals.perAM._default.weeklyMeetingGoal = val; goalsApplied++; }
      else if (key === "per_recruiter_weekly_subs" && scope === "Per Recruiter") { goals.perRecruiter._default.weeklySubGoal = val; goalsApplied++; }
    }

    const data = {
      activeContracts: ac.map(pg => ({
        weeklySpread: P.num(pg, "Weekly Spread"), weeklyRevenue: P.num(pg, "Weekly Revenue"),
        status: P.sel(pg, "Status"), amOwner: P.sel(pg, "AM Owner"), recruiter: P.text(pg, "Recruiter"),
        division: P.sel(pg, "Division"), startDate: P.date(pg, "Start Date"),
        // Prefer the ACTUAL end date when Comtrak syncs it; fall back to scheduled End Date until then.
        endDate: P.date(pg, "Actual End Date") ?? P.date(pg, "End Date"),
        scheduledEndDate: P.date(pg, "End Date"), actualEndDate: P.date(pg, "Actual End Date"),
        consultant: P.text(pg, "Consultant"), account: P.text(pg, "Account (raw)"),
      })),
      recruiterDaily: rd.map(pg => ({
        recruiter: P.text(pg, "Recruiter"), date: P.date(pg, "Date"), subs: P.num(pg, "Subs"),
        calls: P.num(pg, "Calls"), screens: P.num(pg, "Screens"), intSched: P.num(pg, "Int Sched"), offers: P.num(pg, "Offers"),
      })),
      amWeekly: am.map(pg => ({
        am: P.sel(pg, "AM"), date: P.date(pg, "Date"), activityType: P.sel(pg, "Activity Type"), account: P.text(pg, "Account (raw)"),
      })),
      openReqs: or_.map(pg => ({
        amOwner: P.sel(pg, "AM Owner"), status: P.sel(pg, "Status"), openings: P.num(pg, "Openings"),
        filled: P.num(pg, "Filled"), daysOpen: P.num(pg, "Days Open"), agingBucket: P.sel(pg, "Aging Bucket"), division: P.sel(pg, "Division"),
      })),
      placementEvents: pe.map(pg => ({
        eventType: P.sel(pg, "Event Type"), eventDate: P.date(pg, "Event Date"), amOwner: P.sel(pg, "AM Owner"),
        recruiter: P.text(pg, "Recruiter"), division: P.sel(pg, "Division"), consultant: P.text(pg, "Consultant"),
      })),
      innovienNext: inx.map(pg => ({
        matchStatus: P.sel(pg, "Match Status"), amOwner: P.text(pg, "AM Owner"), recruiterOwner: P.text(pg, "Recruiter Owner"),
      })),
      esf: esf.map(pg => ({
        status: P.sel(pg, "Status"), startDate: P.date(pg, "Start Date"), expectedStart: P.date(pg, "Expected Start"),
        created: P.date(pg, "Created"), weeklySpread: P.num(pg, "Weekly Spread"), amOwner: P.sel(pg, "AM Owner"), recruiter: P.text(pg, "Recruiter"),
        candidate: P.text(pg, "Candidate"), client: P.text(pg, "Client"),
      })),
      psf: psf.map(pg => ({
        status: P.sel(pg, "Status"), startDate: P.date(pg, "Start Date"), created: P.date(pg, "Created"),
        weeklyContrib: P.num(pg, "Weekly Contrib"), totalSpread: P.num(pg, "Total Spread"), amOwner: P.sel(pg, "AM Owner"), recruiter: P.text(pg, "Recruiter"),
        candidate: P.text(pg, "Candidate"), client: P.text(pg, "Client"),
      })),
    };

    const asOf = new Date().toISOString().slice(0, 10);
    const result = buildScorecard(data, goals, asOf, weekly);
    result.rowCounts = {
      activeContracts: ac.length, recruiterDaily: rd.length, amWeekly: am.length,
      openReqs: or_.length, placementEvents: pe.length, innovienNext: inx.length,
      esf: esf.length, psf: psf.length, goals: goalRows.length,
    };
    result.goalsApplied = goalsApplied;
    result.weeklyDataWeekEnding = weekly?.meta?.week_ending || null;
    result.generatedAt = new Date().toISOString();
    cache = { at: Date.now(), payload: result };
    return res.status(200).json({ ...result, cached: false });
  } catch (err) {
    return res.status(500).json({ error: String(err.message || err) });
  }
}
