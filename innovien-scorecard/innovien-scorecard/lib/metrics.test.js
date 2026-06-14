import { buildScorecard } from "./metrics.js";
import assert from "node:assert";

const goals = {
  quarterLabel: "Q2 2026", baselineWeekStart: "2026-03-23", quarterStart: "2026-03-30", quarterEnd: "2026-06-28",
  lookbackWeeks: 13,
  company: { weeklySpreadGoal: 350000, qtrStartsGoal: 60, avgStartGoal: 1300, weeklySubGoal: 40, qtrlyMeetingGoal: 260, fillRatioGoal: 0.35, redeployedGoal: 12, yearSpreadGoal: 500000, avgStartGoal: 1300, pendingAvgGoal: 1300, weeklyLockupCountGoal: 2, weeklyLockupSpreadGoal: 3000 },
  perAM: { _default: { weeklyMeetingGoal: 5, fillRatioGoal: 0.35 } },
  perRecruiter: { _default: { weeklySubGoal: 4 } },
  raffle: { threshold: 1250, batchSize: 2, programStart: "2026-01-01" },
};

const data = {
  activeContracts: [
    { weeklySpread: 1000, status: "Active",      amOwner: "Mollie Ferguson", recruiter: "Brennan", startDate: "2026-05-04", endDate: "2026-12-31" },
    { weeklySpread: 1500, status: "Ending Soon", amOwner: "Jon Pack",        recruiter: "Kelsie",  startDate: "2026-01-01", endDate: "2026-06-22" },
    { weeklySpread: 9999, status: "Rolled Off",  amOwner: "Jon Pack",        recruiter: "Kelsie",  startDate: "2026-01-01", endDate: "2026-02-01" },
  ],
  recruiterDaily: [
    { recruiter: "Brennan", date: "2026-06-08", subs: 3 },
    { recruiter: "Brennan", date: "2026-06-09", subs: 2 },
    { recruiter: "Kelsie",  date: "2026-06-09", subs: 4 },
    { recruiter: "Brennan", date: "2020-01-01", subs: 99 }, // outside lookback -> excluded
  ],
  amWeekly: [
    { am: "Mollie Ferguson", date: "2026-06-08", activityType: "Meeting" },
    { am: "Mollie Ferguson", date: "2026-06-09", activityType: "Call" },   // not a meeting
    { am: "Jon Pack",        date: "2026-06-09", activityType: "Meeting" },
  ],
  openReqs: [
    { amOwner: "Jon Pack", status: "Open", openings: 4, filled: 0, agingBucket: ">90d" },
    { amOwner: "Mollie Ferguson", status: "Open", openings: 2, filled: 2, agingBucket: "<=14d" },
    { amOwner: "Jon Pack", status: "Closed", openings: 5, filled: 5, agingBucket: ">90d" }, // excluded
  ],
  placementEvents: [
    { eventType: "Started", eventDate: "2026-05-10", amOwner: "Mollie Ferguson", recruiter: "Brennan" },
    { eventType: "Started", eventDate: "2026-06-01", amOwner: "Jon Pack",        recruiter: "Kelsie" },
    { eventType: "Ended",   eventDate: "2026-06-01", amOwner: "Jon Pack",        recruiter: "Kelsie" }, // not a start
    { eventType: "Started", eventDate: "2026-01-01", amOwner: "Jon Pack",        recruiter: "Kelsie" }, // before quarter
  ],
  esf: [
    { status: "Started",         startDate: "2026-05-01", created: "2026-04-25", weeklySpread: 1000, amOwner: "Jon Pack", recruiter: "Kelsie" }, // started in qtr
    { status: "Started",         startDate: "2026-06-13", created: "2026-06-10", weeklySpread: 1400, amOwner: "Mollie Ferguson", recruiter: "Brennan" }, // created this week (asOf 6/14, Mon 6/8)
    { status: "Pending Payroll", startDate: "2026-06-25", created: "2026-06-09", weeklySpread: 800,  amOwner: "Jon Pack", recruiter: "Kelsie" }, // pending future in qtr, created this week
    { status: "Cancelled",       startDate: "2026-06-25", created: "2026-06-09", weeklySpread: 9999, amOwner: "Jon Pack", recruiter: "Kelsie" }, // excluded
  ],
  psf: [
    { status: "Started",                startDate: "2026-04-15", created: "2026-04-10", weeklyContrib: 500, amOwner: "Mollie Ferguson", recruiter: "Brennan" }, // started in qtr
    { status: "Pending Office Manager", startDate: "2026-07-15", created: "2026-06-01", weeklyContrib: 600, amOwner: "Jon Pack", recruiter: "Kelsie" }, // future BEYOND qtr -> not pending(in qtr), not dumpin
  ],
  innovienNext: [
    { matchStatus: "Placed" }, { matchStatus: "Placed" }, { matchStatus: "Available" }, { matchStatus: "Searching" },
  ],
};

const r = buildScorecard(data, goals, "2026-06-14");

assert.strictEqual(r.scorecard.weeklySpread.actual, 2500, "weeklySpread should sum Active+Ending Soon only");
assert.strictEqual(r.scorecard.activeConsultants, 2, "active count excludes Rolled Off");
assert.strictEqual(r.scorecard.redeployed.actual, 2, "2 placed");
assert.strictEqual(r.scorecard.availableBench, 2, "available + searching");
assert.strictEqual(r.goalTracking.qtrlyMeetingPace.actual, 2, "2 meetings in quarter");
assert.strictEqual(r.goalTracking.fillRatio.actual, 0.333, "filled2/openings6 on open reqs");
assert.strictEqual(r.openReqHealth.totalOpen, 2);
assert.strictEqual(r.openReqHealth.reqsNoFill, 1);
const brennan = r.goalTracking.recruiterSubAvg.find(x => x.name === "Brennan");
assert.strictEqual(brennan.total, 5, "Brennan 3+2 within lookback (99 excluded)");
assert.ok(r.scorecard.forecast.length > 0, "forecast weeks present");
assert.ok("cumNet" in r.scorecard.forecast[0] && "isPast" in r.scorecard.forecast[0], "forecast weeks carry cumNet + isPast");
assert.ok(r.scorecard.forecast.some(w => w.isPast), "some historical weeks flagged isPast");
assert.strictEqual(r.scorecard.forecast[0].weekStart, "2026-03-30", "chart starts at quarter week 1, NOT the baseline week");
assert.ok(!r.scorecard.forecast.some(w=>w.weekStart < "2026-03-30"), "baseline week (3/23) excluded from quarter actuals");
assert.strictEqual(r.meta.quarterStart, "2026-03-30", "quarter window start is separate from baseline");
const outWeek = r.scorecard.forecast.find(w => w.plannedOut === 1500);
assert.ok(outWeek, "the Ending Soon 1500 contract rolls off in some quarter week");
// --- ESF/PSF scorecard tiles ---
assert.strictEqual(r.scorecard.netNewStarts.actual, 3, "started-in-qtr: ESF 5/1 + ESF 6/13 + PSF 4/15");
assert.strictEqual(r.scorecard.avgStartSpread.actual, Math.round((1000+1400+500)/3), "avg weekly spread of the 3 started");
assert.strictEqual(r.scorecard.pendingStarts.count, 1, "pending future within qtr = ESF 6/25 only (PSF 7/15 is beyond qtr)");
assert.strictEqual(r.scorecard.pendingStarts.avgSpread, 800, "pending avg = the 800 ESF");
assert.strictEqual(r.scorecard.weeklyLockUp.count, 2, "created this week (6/8-6/14): ESF 6/13 created 6/10 + ESF 6/25 created 6/9");
assert.strictEqual(r.scorecard.weeklyLockUp.spread, 1400+800, "lockup spread");
assert.strictEqual(r.scorecard.dumpIn.count, 4, "in-qtr starts: ESF 5/1,6/13,6/25 + PSF 4/15 (PSF 7/15 excluded)");
assert.strictEqual(r.scorecard.dumpIn.spread, 1000+1400+800+500, "dump-in spread = netNew + pending(in qtr)");
assert.strictEqual(r.scorecard.dumpIn.count, r.scorecard.netNewStarts.actual + r.scorecard.pendingStarts.count, "dump-in = started + pending(in qtr)");
// --- $1,250 Raffle ---
assert.strictEqual(r.raffle.qualifyingCount, 1, "only the 1400 ESF qualifies (>=1250, started, since program start)");
assert.strictEqual(r.raffle.totalTickets, 2, "AM + recruiter each get a ticket = 2");
assert.strictEqual(r.raffle.batchSize, 2, "batch size from config");
assert.strictEqual(r.raffle.drawingsEarned, 0, "1 qualifying < batch 2 => no drawing yet");
assert.strictEqual(r.raffle.startsToNext, 1, "need 1 more to hit batch of 2");
assert.ok(r.raffle.leaderboard.find(x=>x.name==="Mollie Ferguson" && x.asAM===1), "Mollie earns an AM ticket");
assert.ok(r.raffle.leaderboard.find(x=>x.name==="Brennan" && x.asRecruiter===1), "Brennan earns a recruiter ticket");
// --- Canonical weekly-data override ---
const r2 = buildScorecard(data, goals, "2026-06-14", { company:{ weekly_spread: 324317 }, scorecard:{ net_new_starts: 42, pending_total_spread: 88000, active_consultants: 279, lockup_spread: null } });
assert.strictEqual(r2.scorecard.weeklySpread.actual, 324317, "weekly_spread pinned from canonical file");
assert.strictEqual(r2.scorecard.netNewStarts.actual, 42, "net_new_starts pinned");
assert.strictEqual(r2.scorecard.pendingStarts.totalSpread, 88000, "pending total pinned");
assert.strictEqual(r2.scorecard.activeConsultants, 279, "active consultants pinned");
assert.strictEqual(r2.scorecard.weeklyLockUp.spread, r.scorecard.weeklyLockUp.spread, "null field falls back to live Notion value");
assert.strictEqual(r2.goalTracking.weeklySubAvg.actual, r.goalTracking.weeklySubAvg.actual, "other tabs unaffected by scorecard override");
console.log("ALL TESTS PASSED ✅  (weeklySpread, starts, fill ratio, meetings, subs, forecast, bench)");
console.log(JSON.stringify({ weeklySpread: r.scorecard.weeklySpread, fillRatio: r.goalTracking.fillRatio, forecastWeeks: r.scorecard.forecast.length }, null, 2));
