# Go-Live Checklist — Innovien Weekly Stretch Scorecard

Three phases: (A) give the app read access to Notion, (B) confirm the goal numbers,
(C) deploy to Vercel. ~20–30 minutes total.

## A. Connect Notion (one time)
1. Go to https://www.notion.so/my-integrations → **New integration**.
   - Type: Internal · Capabilities: **Read content** only · Associated workspace: Innovien.
   - Copy the **Internal Integration Secret** (starts with `ntn_`). Keep it private.
2. Give the integration access to the data. Easiest: open **Teamspace Home** in Notion →
   **•••** (top right) → **Connections** → add your integration. Access cascades to the
   child databases below. (If your workspace doesn't cascade, add it on each DB individually.)

   Databases the dashboard reads:
   - Active Contracts
   - ESF Pipeline
   - PSF Pipeline
   - Recruiter Daily Activity
   - AM Weekly Activity
   - Open Reqs            (used for the Q2 AM Fill Ratio)
   - Placement Events
   - Innovien Next
   - Company Goals

## B. Confirm goals (one time per quarter)
- Open the **Company Goals** database. The 14 seeded rows are marked PLACEHOLDER.
- Set the real `Value` for each, especially: weekly_spread, qtr_starts, avg_start_spread,
  pending_avg_spread, weekly_lockup_count, weekly_lockup_spread.
- Keep `Active` checked and `Period` = the current quarter (e.g. "Q2 2026").
- Edit `goals.json` dates: `baselineWeekStart` (spread-growth anchor, e.g. 2026-03-23),
  `quarterStart`/`quarterEnd` (actual quarter window, e.g. 2026-03-30 to 2026-06-28), `quarterLabel`,
  and `raffle.programStart`.

## C. Deploy to Vercel
Option 1 — GitHub (best for ongoing edits):
1. Push this `innovien-scorecard` folder to a private GitHub repo.
2. vercel.com → **Add New → Project** → import the repo.
3. Project **Settings → Environment Variables** → add `NOTION_TOKEN` = the `ntn_` secret
   (Production + Preview). The DB IDs are already baked in (override via DB_* vars only if they change).
4. **Deploy.** You'll get a URL like `https://innovien-scorecard.vercel.app` to share.
   Future edits: push to GitHub → Vercel redeploys automatically.

Option 2 — Vercel CLI (fastest one-off):
```
npm i -g vercel
cd innovien-scorecard
vercel              # link/create the project
vercel env add NOTION_TOKEN     # paste the ntn_ secret
vercel --prod
```

## D. Verify it's live
- Open the URL. The yellow "sample data" banner should be GONE.
- The footer shows row counts (e.g. "210 contracts · 105 reqs …") and goals applied.
- Add `?refresh=1` to the URL to bypass the 5-minute cache and pull fresh Notion data.
- The page has a "↻ Refresh live data" button; data otherwise caches 5 min server-side.

## Notes
- The Comtrak agent updates Notion daily, so the scoreboard reflects yesterday's close.
- Token lives only in Vercel env vars — never in the code or the repo.
- Custom domain (e.g. scoreboard.innovien.com) can be added in Vercel → Settings → Domains.
