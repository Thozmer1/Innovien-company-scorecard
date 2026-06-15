// Pure aggregation logic. Operates on plain mapped objects (no Notion coupling),
// so it can be unit-tested with fixtures. Mirrors the Power BI "Weekly Stretch Wkbk" measures.

const DAY = 86400000;
const d = (s) => (s ? new Date(s + (s.length === 10 ? "T00:00:00Z" : "")) : null);
const within = (dateStr, start, end) => {
  const t = d(dateStr); if (!t) return false;
  return (!start || t >= start) && (!end || t <= end);
};
const round = (n, p = 0) => { const f = 10 ** p; return Math.round((n + Number.EPSILON) * f) / f; };
const ACTIVE = new Set(["Active", "Ending Soon"]);
// A start is "banked" only when confirmed: Notion uses "Started", the Power BI export uses "Complete".
const BANKED_STATUS = new Set(["Started", "Complete"]);

// Combine ESF + PSF into one "start form" list. PSF uses Weekly Contrib as its weekly spread.
function unifyStarts(esf = [], psf = []) {
  const out = [];
  for (const e of esf) out.push({ type: "ESF", status: e.status, startDate: e.startDate, created: e.created, weeklySpread: e.weeklySpread || 0, amOwner: e.amOwner, recruiter: e.recruiter, consultant: e.candidate, client: e.client });
  for (const p of psf) out.push({ type: "PSF", status: p.status, startDate: p.startDate, created: p.created, weeklySpread: p.weeklyContrib || 0, amOwner: p.amOwner, recruiter: p.recruiter, consultant: p.candidate, client: p.client });
  return out.filter(r => r.status !== "Cancelled");
}
const sumSpread = rows => round(rows.reduce((s, r) => s + (r.weeklySpread || 0), 0));
const avgSpread = rows => rows.length ? round(sumSpread(rows) / rows.length) : 0;


// group-sum helper
function bucketSum(rows, keyFn, valFn) {
  const m = new Map();
  for (const r of rows) {
    const k = keyFn(r); if (k == null || k === "Unassigned") continue;
    const v = valFn(r) || 0;
    m.set(k, (m.get(k) || 0) + v);
  }
  return m;
}

export function buildScorecard(data, goals, asOfStr, weekly) {
  const asOf = d(asOfStr) || new Date();
  const qStart = d(goals.quarterStart);
  const qEnd = d(goals.quarterEnd);
  const lookback = goals.lookbackWeeks || 13;
  const lookbackStart = new Date(asOf.getTime() - lookback * 7 * DAY);
  const g = goals.company;

  const { activeContracts = [], recruiterDaily = [], amWeekly = [], openReqs = [], placementEvents = [], innovienNext = [] } = data;

  // ---------- STRETCH SCORECARD (page 1) ----------
  // Tile 1: Weekly Spread = run-rate of active placements (Active Contracts).
  const live = activeContracts.filter(c => ACTIVE.has(c.status));
  const weeklySpread = round(live.reduce((s, c) => s + (c.weeklySpread || 0), 0));
  const activeCount = live.length;

  // Start forms: ESF + PSF unified, Cancelled removed. Classify by Start Date vs today.
  const starts = unifyStarts(data.esf, data.psf);
  const today = asOf;
  const inQtr = ds => within(ds, qStart, qEnd);
  const dated = starts.filter(r => r.startDate);

  // Tile 2/3: Net New Starts this quarter (already started: start date in [qStart, today]).
  const netNew = dated.filter(r => { const t = d(r.startDate); return BANKED_STATUS.has(r.status) && t >= qStart && t <= today; });
  const qtrStarts = netNew.length;
  const avgStartSpread = avgSpread(netNew);

  // Tile 4: Pending starts = future-dated within the quarter (today, qEnd].
  const pending = dated.filter(r => { const t = d(r.startDate); return !BANKED_STATUS.has(r.status) && t >= today && t <= qEnd; });
  const pendingCount = pending.length;
  const pendingAvgSpread = avgSpread(pending);
  const pendingSpread = sumSpread(pending);

  // Tile 5: Weekly Lock-Up = new ESF/PSF *created* in the current Mon-Sun week.
  const wkStart = mondayOf(today); const wkEnd = new Date(wkStart.getTime() + 7 * DAY - 1);
  const lockup = starts.filter(r => { const t = d(r.created); return t && t >= wkStart && t <= wkEnd; });
  const lockupCount = lockup.length;
  const lockupSpread = sumSpread(lockup);

  // Tile 6: Dump-In = all (non-cancelled) starts with start date in the quarter = netNew + pending.
  const dumpIn = dated.filter(r => inQtr(r.startDate));
  const dumpInCount = dumpIn.length;
  const dumpInSpread = sumSpread(dumpIn);

  const redeployed = innovienNext.filter(p => p.matchStatus === "Placed").length;
  const availableBench = innovienNext.filter(p => ["Available", "Searching", "Warm Match Found", "Outreach Sent", "Interview"].includes(p.matchStatus)).length;

  // Spread In/Out forecast: by ISO week across the quarter
  // Chart shows the ACTUAL quarter weeks only. The baseline week is a reference point and is
  // excluded from every quarter actual; the cumulative line = spread growth from the baseline level
  // (i.e. starts at $0 at quarter start).
  const forecast = forecastByWeek(activeContracts, qStart, qEnd, asOf);

  // ---------- Q2 GOAL TRACKING (page 2) ----------
  const meetings13 = amWeekly.filter(a => a.activityType === "Meeting" && within(a.date, lookbackStart, asOf));
  const amMeetingTotals = bucketSum(meetings13, r => r.am, () => 1);
  const amMeetingAvg = mapToRows(amMeetingTotals, (am, total) => ({
    name: am, weeklyAvg: round(total / lookback, 1), total,
    goal: goalFor(goals.perAM, am, "weeklyMeetingGoal"),
  }));

  const subs13 = recruiterDaily.filter(r => within(r.date, lookbackStart, asOf));
  const recSubTotals = bucketSum(subs13, r => r.recruiter, r => r.subs);
  const recruiterSubAvg = mapToRows(recSubTotals, (rec, total) => ({
    name: rec, weeklyAvg: round(total / lookback, 1), total: round(total),
    goal: goalFor(goals.perRecruiter, rec, "weeklySubGoal"),
  }));

  const totalSubs13 = subs13.reduce((s, r) => s + (r.subs || 0), 0);
  const weeklySubAvg = round(totalSubs13 / lookback, 1);

  const meetingsQtr = amWeekly.filter(a => a.activityType === "Meeting" && within(a.date, qStart, qEnd)).length;

  // Fill ratio = filled / openings on open reqs
  const openReqRows = openReqs.filter(r => r.status === "Open");
  const totOpenings = openReqRows.reduce((s, r) => s + (r.openings || 0), 0);
  const totFilled = openReqRows.reduce((s, r) => s + (r.filled || 0), 0);
  const fillRatio = totOpenings ? round(totFilled / totOpenings, 3) : 0;

  const amOpenings = bucketSum(openReqRows, r => r.amOwner, r => r.openings);
  const amFilled = bucketSum(openReqRows, r => r.amOwner, r => r.filled);
  const amFillRatio = mapToRows(amOpenings, (am, openings) => {
    const filled = amFilled.get(am) || 0;
    return { name: am, ratio: openings ? round(filled / openings, 3) : 0, filled, openings,
      goal: goalFor(goals.perAM, am, "fillRatioGoal") };
  });

  // Fill ratio override from the PBI "Close Ratio Details" tab (via weekly_data.json fill_ratio).
  // Tile = company QTD; AM table = trailing 13 weeks. Falls back to live Open Reqs when absent.
  let fillRatioV = fillRatio, amFillRatioV = amFillRatio;
  if (weekly && weekly.fill_ratio) {
    const fr = weekly.fill_ratio;
    if (fr.company && fr.company.ratio != null) fillRatioV = round(fr.company.ratio, 3);
    if (Array.isArray(fr.by_am)) amFillRatioV = fr.by_am.map(a => ({
      name: a.name, ratio: round(a.ratio, 3), filled: a.filled, openings: a.openings,
      goal: goalFor(goals.perAM, a.name, "fillRatioGoal"),
    }));
  }

  // ---------- $1,250 RAFFLE (page 3) ----------
  const rcfg = goals.raffle || { threshold: 1250, batchSize: 15, programStart: goals.quarterStart };
  const progStart = d(rcfg.programStart);
  const batch = rcfg.batchSize || 15;
  const thr = rcfg.threshold || 1250;
  // Qualifying = STARTED (start date <= today, not cancelled), since program start, weekly spread >= threshold.
  const qualifying = dated.filter(r => {
    const t = d(r.startDate);
    return BANKED_STATUS.has(r.status) && t <= today && (!progStart || t >= progStart) && (r.weeklySpread || 0) >= thr;
  }).sort((a, b) => (a.startDate < b.startDate ? 1 : -1));
  const qualifyingCount = qualifying.length;
  const drawingsEarned = Math.floor(qualifyingCount / batch);
  const towardNext = qualifyingCount % batch;
  const startsToNext = towardNext === 0 ? batch : batch - towardNext;
  const progressPct = round((towardNext / batch) * 100);
  const nextDrawingAt = (drawingsEarned + 1) * batch;

  // Tickets: AM + recruiter each earn one per qualifying start.
  const tix = new Map();
  const bump = (name, role) => { if (!name || name === "Unassigned") return; const e = tix.get(name) || { asAM: 0, asRecruiter: 0 }; e[role]++; tix.set(name, e); };
  qualifying.forEach(r => { bump(r.amOwner, "asAM"); bump(r.recruiter, "asRecruiter"); });
  const leaderboard = [...tix.entries()].map(([name, e]) => ({ name, tickets: e.asAM + e.asRecruiter, asAM: e.asAM, asRecruiter: e.asRecruiter })).sort((a, b) => b.tickets - a.tickets);
  const totalTickets = leaderboard.reduce((s2, x) => s2 + x.tickets, 0);

  // On deck: future-dated qualifying (not yet started) since program start.
  const onDeck = dated.filter(r => { const t = d(r.startDate); return t > today && (r.weeklySpread || 0) >= thr; });
  const onDeckCount = onDeck.length;

  const qualifyingList = qualifying.slice(0, 60).map(r => ({
    consultant: r.consultant || "", client: r.client || "", amOwner: r.amOwner || "Unassigned",
    recruiter: r.recruiter || "", weeklySpread: Math.round(r.weeklySpread || 0), startDate: r.startDate, type: r.type,
  }));

  const raffle = {
    threshold: thr, batchSize: batch, programStart: rcfg.programStart,
    qualifyingCount, drawingsEarned, towardNext, startsToNext, progressPct, nextDrawingAt,
    totalTickets, onDeckCount, leaderboard, qualifyingList,
  };

  // ---------- OPEN REQ HEALTH ----------
  const aging = { "<=14d": 0, "15-45d": 0, "46-90d": 0, ">90d": 0 };
  for (const r of openReqRows) if (r.agingBucket in aging) aging[r.agingBucket]++;
  const reqsNoFill = openReqRows.filter(r => (r.filled || 0) === 0).length;

  const kpi = (actual, goal, fmt) => ({ actual, goal, pct: goal ? round((actual / goal) * 100) : null, onPace: goal ? actual >= goal : null, fmt });

  // --- Canonical weekly-data overrides (Scorecard tab). null/undefined = keep live Notion value. ---
  const _wco = (weekly && weekly.company) || {};
  const _wsc = (weekly && weekly.scorecard) || {};
  const ov = (v, cur) => (v === null || v === undefined) ? cur : v;
  const oWeeklySpread = ov(_wco.weekly_spread, weeklySpread);
  const oNetNew       = ov(_wsc.net_new_starts, qtrStarts);
  const oAvgStart     = ov(_wsc.avg_start_spread, avgStartSpread);
  const oPendCount    = ov(_wsc.pending_count, pendingCount);
  const oPendAvg      = ov(_wsc.pending_avg_spread, pendingAvgSpread);
  const oPendTot      = ov(_wsc.pending_total_spread, pendingSpread);
  const oLockCount    = ov(_wsc.lockup_count, lockupCount);
  const oLockSpread   = ov(_wsc.lockup_spread, lockupSpread);
  const oLockSpreadGoal = ov(_wsc.lockup_spread_goal, g.weeklyLockupSpreadGoal);
  const oLockCountGoal  = ov(_wsc.lockup_count_goal, g.weeklyLockupCountGoal);
  const oDumpCount    = ov(_wsc.dumpin_count, dumpInCount);
  const oDumpSpread   = ov(_wsc.dumpin_spread, dumpInSpread);
  const oActive       = ov(_wsc.active_consultants, activeCount);
  const oRedeployed   = ov(_wsc.redeployed, redeployed);
  const oBench        = ov(_wsc.available_bench, availableBench);
  let oForecast = forecast;
  if (_wsc.forecast && _wsc.forecast.length) {
    // File-provided weekly In/Out (e.g. from the Power BI Stretch scorecard cols W/X).
    // Compute running cumulative + past/forecast flag so the chart renders identically.
    let _cum = 0;
    oForecast = _wsc.forecast.map(w => {
      const pin = w.plannedIn || 0, pout = w.plannedOut || 0, net = pin - pout; _cum += net;
      const wd = d(w.weekStart);
      return { weekStart: w.weekStart, plannedIn: Math.round(pin), plannedOut: Math.round(pout),
               net: Math.round(net), cumNet: Math.round(_cum), isPast: wd ? wd < asOf : false };
    });
  }
  const pct = (a, go) => go ? round((a / go) * 100) : null;
  const onp = (a, go) => go ? a >= go : null;

  return {
    meta: {
      asOf: asOfStr, quarterLabel: goals.quarterLabel,
      baselineWeekStart: goals.baselineWeekStart, quarterStart: goals.quarterStart, quarterEnd: goals.quarterEnd, lookbackWeeks: lookback,
    },
    scorecard: {
      weeklySpread: kpi(oWeeklySpread, g.weeklySpreadGoal, "usd"),
      netNewStarts: kpi(oNetNew, g.qtrStartsGoal, "int"),
      avgStartSpread: kpi(oAvgStart, g.avgStartGoal, "usd"),
      pendingStarts: { count: oPendCount, avgSpread: oPendAvg, totalSpread: oPendTot,
        avgGoal: g.pendingAvgGoal, avgPct: pct(oPendAvg, g.pendingAvgGoal), avgOnPace: onp(oPendAvg, g.pendingAvgGoal) },
      weeklyLockUp: { count: oLockCount, spread: oLockSpread,
        countGoal: oLockCountGoal, spreadGoal: oLockSpreadGoal,
        countPct: pct(oLockCount, oLockCountGoal), countOnPace: onp(oLockCount, oLockCountGoal),
        spreadPct: pct(oLockSpread, oLockSpreadGoal), spreadOnPace: onp(oLockSpread, oLockSpreadGoal),
        weekStart: wkStart.toISOString().slice(0,10) },
      dumpIn: { count: oDumpCount, spread: oDumpSpread },
      activeConsultants: oActive,
      redeployed: kpi(oRedeployed, g.redeployedGoal, "int"),
      availableBench: oBench,
      forecast: oForecast,
    },
    goalTracking: {
      weeklySubAvg: kpi(weeklySubAvg, g.weeklySubGoal, "dec"),
      qtrlyMeetingPace: kpi(meetingsQtr, g.qtrlyMeetingGoal, "int"),
      fillRatio: kpi(fillRatioV, g.fillRatioGoal, "pct"),
      amMeetingAvg: amMeetingAvg.sort((a, b) => b.weeklyAvg - a.weeklyAvg),
      recruiterSubAvg: recruiterSubAvg.sort((a, b) => b.weeklyAvg - a.weeklyAvg),
      amFillRatio: amFillRatioV.sort((a, b) => b.ratio - a.ratio),
    },
    raffle,
    openReqHealth: { totalOpen: openReqRows.length, aging, reqsNoFill, totOpenings, totFilled },
  };
}

function forecastByWeek(contracts, anchorStart, qEnd, asOf) {
  if (!anchorStart || !qEnd) return [];
  const weeks = [];
  let cur = mondayOf(anchorStart);
  let cum = 0;
  while (cur <= qEnd) {
    const wEnd = new Date(cur.getTime() + 7 * DAY - 1);
    let inSpread = 0, outSpread = 0, inCount = 0, outCount = 0;
    for (const c of contracts) {
      // IN  = placements whose Start Date falls in this week.
      // OUT = placements whose End Date falls in this week (ALL statuses, incl. Rolled Off),
      //       so historical weeks reflect spread that left the book.
      const sd = c.startDate ? new Date(c.startDate + "T00:00:00Z") : null;
      const ed = c.endDate ? new Date(c.endDate + "T00:00:00Z") : null;
      if (sd && sd >= cur && sd <= wEnd) { inSpread += c.weeklySpread || 0; inCount++; }
      if (ed && ed >= cur && ed <= wEnd) { outSpread += c.weeklySpread || 0; outCount++; }
    }
    const net = inSpread - outSpread; cum += net;
    weeks.push({
      weekStart: cur.toISOString().slice(0, 10),
      plannedIn: Math.round(inSpread), plannedOut: Math.round(outSpread),
      inCount, outCount, net: Math.round(net), cumNet: Math.round(cum),
      isPast: wEnd < asOf,
    });
    cur = new Date(cur.getTime() + 7 * DAY);
  }
  return weeks;
}
function mondayOf(dt) {
  const t = new Date(dt.getTime());
  const day = (t.getUTCDay() + 6) % 7; // Mon=0
  t.setUTCDate(t.getUTCDate() - day); t.setUTCHours(0, 0, 0, 0);
  return t;
}
function mapToRows(map, fn) { return [...map.entries()].map(([k, v]) => fn(k, v)); }
function goalFor(section, name, key) {
  if (!section) return null;
  return (section[name] && section[name][key]) ?? (section._default && section._default[key]) ?? null;
}
