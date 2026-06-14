# Innovien · Weekly Stretch Scorecard (live, Notion-fed)

A web recreation of the Power BI **"Weekly Stretch Wkbk"** daily company scoreboard.
It reads **live** from the Comtrak-fed Notion databases and renders the scorecard pages in the browser.
Deploys to **Vercel** so you get one shareable link that refreshes itself.

## What's live vs. manual
- **Live (from Comtrak → Notion):** Weekly Spread, Qtr Starts, Start Avg, Active Consultants,
  AM Meeting Avg, Recruiter Sub Avg, Weekly Sub Avg, Meeting Pace, Fill Ratio, Net New Starts,
  Spread In/Out forecast, Open Req health, Redeployed/Available bench.
- **Manual (you set them):** all goals/targets — they don't exist in Comtrak. Set them in the Notion **Company Goals** database (one row per metric, by quarter). `goals.json` is only a fallback.
- **Not included:** QuickBooks financials (P&L, invoices, collections) and the submittal→interview→offer
  funnel — those aren't in Notion's Comtrak sync yet. See `MAPPING.md`.

## One-time setup
1. **Create a Notion integration:** https://www.notion.so/my-integrations → *New integration*
   (Internal, capability: *Read content*). Copy the token (`ntn_…`).
2. **Share the 6 databases with it:** open each DB → *•••* → *Connections* → add your integration:
   Active Contracts · ESF Pipeline · PSF Pipeline · Recruiter Daily Activity · AM Weekly Activity · Open Reqs · Placement Events · Innovien Next · Company Goals.
   (IDs are already baked into `lib/notion.js` / `.env.example`.)
3. **Deploy:**
   ```bash
   npm i -g vercel
   vercel            # first run: link/create project
   vercel env add NOTION_TOKEN   # paste the ntn_ token (Production + Preview)
   vercel --prod
   ```
   Or push this folder to GitHub and "Import Project" in the Vercel dashboard, then add the
   `NOTION_TOKEN` env var. You'll get a URL like `https://innovien-scorecard.vercel.app`.

## Updating each quarter
1. **Dates** in `goals.json` (then commit/redeploy):
   - `baselineWeekStart` — Monday of the week you measure spread growth FROM (usually the week before the quarter), e.g. `2026-03-23`.
   - `quarterStart` / `quarterEnd` — the actual quarter window (Mon..Sun), e.g. `2026-03-30` to `2026-06-28`.
   - `quarterLabel` — e.g. `Q3 2026`. Also update `raffle.programStart` if the raffle resets.
   The In/Out growth chart runs from `baselineWeekStart` through `quarterEnd`; the start/pending/dump-in/meeting
   counts use `quarterStart`..`quarterEnd`.
2. **Goal numbers** in the Notion **Company Goals** database (no redeploy needed): change each `Value`,
   set `Period` to the new quarter label, keep `Active` checked.

## Local preview
Open `index.html` directly in a browser — it shows **sample data** with a banner until the
live API is reachable. Run the metric self-test with `npm run selftest`.

## How it works
- `api/scorecard.js` — Vercel serverless function. Queries all 6 DBs via `@notionhq/client`,
  maps + aggregates, returns one JSON payload (5-min server cache; `?refresh=1` to bust).
- `lib/metrics.js` — pure aggregation (mirrors the Power BI measures); unit-tested in `lib/metrics.test.js`.
- `public/index.html` — single-file branded dashboard (Calibri, Innovien palette), Chart.js for the forecast.
