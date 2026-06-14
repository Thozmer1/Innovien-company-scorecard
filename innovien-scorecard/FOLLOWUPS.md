# Dashboard follow-ups

Deferred items to revisit AFTER the remaining edits + Vercel deployment are done.

## 1. Actual End Date on the weekly In/Out chart  (deferred 2026-06-14)
- The Scorecard weekly chart's "out the door" should use the placement's **Actual End Date**.
- Current state: the code already prefers `Actual End Date` and falls back to `End Date`
  (`api/scorecard.js`, Active Contracts mapping). No code change needed.
- Gap: the Notion **Active Contracts** sync does NOT yet carry an `Actual End Date` property —
  only `End Date`. So the chart effectively uses `End Date` until that field exists.
- To finish: have the Comtrak transformation agent add + populate an `Actual End Date` date
  property on Active Contracts. Once present, the chart switches automatically.
