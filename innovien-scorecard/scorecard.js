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
      hours_util_goal: ["company","hoursUtilGoal"],
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

    // ---- Active AM/Recruiter roster (People DB) -------------------------------
    // The Q2 Goal Tracking tab should only show people currently tagged Active AND
    // in an AM/Recruiter role. We read it live so the dashboard tracks Notion.
    // Fail-open: any error here leaves roster null and metrics skips the filter.
    let roster = null;
    try {
      const truthyActive = (v) => {
        if (v === true) return true;
        if (typeof v === "number") return v !== 0;
        if (typeof v === "string") {
          const s = v.trim().toLowerCase();
          if (!s) return false;
          if (["inactive", "no", "false", "n", "0"].includes(s)) return false;
          if (["active", "yes", "true", "y", "1"].includes(s)) return true;
          return s.includes("active") && !s.includes("inactive");
        }
        return false;
      };
      const AM_ROLES = new Set(["am", "account manager", "sr. account manager", "senior account manager"]);
      const REC_ROLES = new Set(["recruiter", "recruiting lead", "sr. recruiter", "senior recruiter"]);
      const norm = (s) => (s || "").toString().trim().replace(/\s+/g, " ").toLowerCase();
      const ppl = await queryAll(notion, DB.people);
      const activeAMs = new Set(), activeRecruiters = new Set();
      for (const pg of ppl) {
        const name = P.text(pg, "Full Name");
        if (!name) continue;
        if (!truthyActive(P.formula(pg, "Active"))) continue;
        const role = norm(P.sel(pg, "Role"));
        if (AM_ROLES.has(role)) activeAMs.add(norm(name));
        else if (REC_ROLES.has(role)) activeRecruiters.add(norm(name));
      }
      roster = { activeAMs, activeRecruiters, peopleRows: ppl.length };
    } catch (e) {
      roster = null; // People DB unreachable → show all (current behavior)
    }

    // ---- Hit List reqs (live from Comtrak Raw - Req Details (API)) ---------------
    // Open reqs sitting in the board's Hit List column. We report count, openings, and a
    // RATE-CARD spread computed per req from (Bill Rate - Pay Rate) x 40 x Openings.
    //
    // Why not openings x company avg start spread (the old method): that multiplier is
    // quarter-to-date and moves on every start, so the tile swung ~80% on 2026-10-05 off three
    // Lockheed placements while the hit list itself had not changed. Per-req rates are a
    // property of the reqs on the board and move only when the board moves.
    //
    // Caveats worth knowing before quoting this number:
    //  - 40 hrs/wk is an assumption; the req table carries no hours field.
    //  - This is RATE CARD, not realized. Comparable ABM placements have landed at ~65-75% of
    //    the nominal rate spread, so treat it as a ceiling.
    //  - Reqs missing either rate contribute openings but no dollars; ratedOpenings says how
    //    many of the openings are actually priced, so the UI can disclose partial coverage.
    //  - Contract-Perm reqs carry no Perm Fee / salary here, so only the contract leg is counted.
    // Fail-open: on any error the tile just omits the hit-list line.
    try {
      const hlRows = await queryAll(notion, DB.reqDetails, { and: [
        { property: "REQ Priority", rich_text: { equals: "Hitlist" } },
        { property: "Job Status", rich_text: { equals: "Open" } },
      ]});
      // Count REMAINING seats, not the req's total size. "Openings" is the original req
      // headcount and "Filled" is how many are already placed against it - the dashboard's own
      // fill ratio uses filled/openings, which only works if Openings is the total. Counting
      // total openings double-counts seats already won: on 2026-10-05 that put GE Vernova 3271
      // (4 openings, 3 filled) in at $5,120 for one winnable seat, and Home Depot 2949 (1
      // opening, 2 filled) in at $1,400 for a seat that does not exist. 12 seats / $18,300
      // became 7 seats / $11,300 once remaining was used - a 38% overstatement.
      //
      // Caveat: Filled appears to be cumulative over the req's life, not currently-on-assignment
      // (2949 has filled > openings). Where a placement has since rolled off, remaining can
      // therefore UNDERstate. Erring low is the right direction for a number labelled potential.
      const HRS_PER_WEEK = 40;
      let hlOpenings = 0, hlTotalOpenings = 0, hlRatedOpenings = 0, hlSpread = 0, hlReqsWithSeats = 0;
      for (const pg of hlRows) {
        const total = P.num(pg, "Openings") || 0;
        const filled = P.num(pg, "Filled") || 0;
        const open = Math.max(0, total - filled);   // remaining, never negative
        const bill = P.num(pg, "Bill Rate");
        const pay  = P.num(pg, "Pay Rate");
        hlTotalOpenings += total;
        hlOpenings += open;
        if (open > 0) hlReqsWithSeats += 1;
        if (open > 0 && bill > 0 && pay > 0 && bill > pay) {
          hlSpread += (bill - pay) * HRS_PER_WEEK * open;
          hlRatedOpenings += open;
        }
      }
      data.hitList = { reqs: hlRows.length, reqsWithSeats: hlReqsWithSeats,
                       openings: hlOpenings, totalOpenings: hlTotalOpenings,
                       ratedOpenings: hlRatedOpenings, spread: Math.round(hlSpread),
                       basis: "rate-card: (bill - pay) x 40h x REMAINING openings (total - filled)" };
    } catch (e) {
      data.hitList = null; // DB not shared / unreachable -> tile just omits the hit-list line
    }

    const asOf = new Date().toISOString().slice(0, 10);
    const result = buildScorecard(data, goals, asOf, weekly, roster);
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
