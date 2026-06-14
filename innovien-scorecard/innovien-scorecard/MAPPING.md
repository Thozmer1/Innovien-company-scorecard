# Power BI "Weekly Stretch Wkbk" → Notion (Comtrak) field map

The .pbix is a thin report wired live to a Power BI cloud dataset. That dataset loads ~24 report
tables that are a mix of **Comtrak** (recruiting) and **QuickBooks** (finance). Below is where each
scorecard metric can come from in the Notion side, which is fed daily by the Comtrak Transformation Agent.

LEGEND:  ✅ live from Notion today   ⚙️ manual goal (set in goals.json)   ❌ not in Notion/Comtrak sync yet

## Page: Stretch Scorecard (rebuilt to your spec — 6 tiles)
Start data now comes from **ESF Pipeline** (contract starts; weekly $ = Weekly Spread) + **PSF Pipeline**
(perm placements; weekly $ = Weekly Contrib), combined, Cancelled excluded. A start is "started" when its
Status = Started and Start Date ≤ today; "pending" = future Start Date.

| Tile | Definition | Notion source | Status |
|---|---|---|---|
| Weekly Spread | Run-rate of active placements | Active Contracts → Weekly Spread (Active/Ending Soon) | ✅ |
| Net New Starts · Qtr | Count of ESF+PSF started (Start Date in [Q-start, today]) | ESF + PSF Pipeline | ✅ |
| Avg Weekly Spread / New Start | Avg weekly $ of those started | ESF Weekly Spread / PSF Weekly Contrib | ✅ |
| Pending Starts (+ avg wkly spread) | Count + avg weekly $ of future-dated ESF/PSF (within quarter) | ESF + PSF Pipeline | ✅ |
| Weekly Lock-Up (count + spread, vs goal) | New ESF/PSF **created** in the current Mon–Sun week | ESF + PSF Pipeline (Created) | ✅ |
| Total Dump-In Spread (+ #) | All ESF/PSF with Start Date in the quarter = started + pending | ESF + PSF Pipeline | ✅ |

> **End-date note:** the chart's "out" reads `Actual End Date` if present, else falls back to `End Date`. As of now the Notion Active Contracts sync only has `End Date` — ask the Comtrak agent owner to add/populate an `Actual End Date` date property and the chart will use it automatically.

| (chart) Weekly Spread In vs Out, by week | Active Contracts Start Date (in) & End Date (out, ALL statuses incl. Rolled Off) × Weekly Spread, per quarter week; historical weeks = actuals, future = forecast; running cumulative-net line | Active Contracts | ✅ |

Goals for these tiles are read live from the **Company Goals** Notion DB (Metric Keys: `weekly_spread`,
`qtr_starts`, `avg_start_spread`, `pending_avg_spread`, `weekly_lockup_count`, `weekly_lockup_spread`).

## Page: Q2 Goal Tracking
| Power BI metric | Notion source | Status |
|---|---|---|
| AM Meeting Avg (13 Wk) | AM Weekly Activity → Activity Type = Meeting, by AM, /13 wk | ✅ |
| Recruiter Sub Avg (13 Wk) | Recruiter Daily Activity → Subs, by recruiter, /13 wk | ✅ |
| Weekly Sub Avg | Recruiter Daily Activity → Subs total /13 wk | ✅ |
| Qtrly Meeting Pace | AM Weekly Activity → Meetings in quarter | ✅ |
| QTD Fill Ratio | Open Reqs → Filled ÷ Openings (Status = Open) | ✅ |
| AM Fill Ratio (13 Wk) | Open Reqs → Filled/Openings by AM | ✅ |
| All *Goal* fields | — | ⚙️ |
| TA Interviews | Recruiter Daily Activity → Int Sched (interviews scheduled) | ✅ |

## Page: $1,250 Raffle (repurposed from "Net New Starts")
Promotion tracker. A **qualifying start** = ESF/PSF placement with weekly spread ≥ $1,250 (config `goals.json` → `raffle`).
Every `batchSize` (15) qualifying STARTED placements triggers a drawing; the AM and Recruiter each earn one ticket per
qualifying start (30 tickets/drawing). Running total since `raffle.programStart`.

| Element | Definition | Notion source | Status |
|---|---|---|---|
| Qualifying Starts | Started (date ≤ today, not cancelled) since program start, weekly spread ≥ $1,250 | ESF + PSF Pipeline | ✅ |
| Drawings Earned | floor(qualifying / 15) | derived | ✅ |
| Progress to next drawing | qualifying mod 15, + starts-to-next | derived | ✅ |
| Tickets In Play | qualifying × 2 (AM + recruiter) | ESF + PSF AM Owner / Recruiter | ✅ |
| On Deck | future-dated qualifying (pending) | ESF + PSF Pipeline | ✅ |
| Ticket Leaderboard | tickets per person (as AM / as recruiter) | ESF + PSF | ✅ |
| Qualifying Starts list | consultant, client, AM, recruiter, weekly spread, start date | ESF (Candidate/Client) + PSF | ✅ |

Config keys (goals.json `raffle`): `threshold` 1250 · `batchSize` 15 · `programStart` 2026-01-01.

## In the Power BI model but NOT in the Notion/Comtrak sync
These come from QuickBooks or aren't synced, so they stay in Power BI (or need a new feed):
P&L Detail Report · Invoice List Report · Collections Report · Expense Transaction Report ·
QB Account Table · Headcount Actuals · Employee Commission Details · ESF Details ·
Submittal→Interview→Offer funnel stages.  → ❌ / future feed.

## Notion databases used (IDs)
- Active Contracts — 644d8fc2-c481-42bf-8763-74ea84fcd389
- Recruiter Daily Activity — 9a031270-90b0-4db1-a691-2ff7bea1a169
- AM Weekly Activity — b5a62657-c3ec-4592-a69f-a1cf3a64081d
- Open Reqs — 808a92ee-9125-4a9b-b45b-0d7192db4449
- Placement Events — 132c6ee8-fb8c-4dda-a83b-80b0b1373a7b
- Innovien Next — c420fa85-b8df-8321-925d-01073f86f699
- ESF Pipeline — 6981fee2-c458-4e09-99ed-a29fe9e4633d
- PSF Pipeline — 08344154-4a4e-47a4-b7ae-f0e322caf834
- Company Goals (new) — 68691cd3-0222-4760-b2ed-8bd07ce528ae
