// scripts/rebuild-weekly.mjs
// CLI entry for the daily GitHub Action. Reads goals.json + the current weekly_data.json,
// rebuilds from the Comtrak Raw Notion tables, and writes weekly_data.json.
//   node scripts/rebuild-weekly.mjs            -> compute + write
//   node scripts/rebuild-weekly.mjs --dry-run  -> compute + print a diff, DO NOT write
import { readFile, writeFile } from "node:fs/promises";
import { Client } from "@notionhq/client";
import { rebuildWeekly } from "../lib/rebuild.mjs";

const DRY = process.argv.includes("--dry-run");
const token = process.env.NOTION_TOKEN;
if (!token) { console.error("ERROR: NOTION_TOKEN is not set."); process.exit(1); }

const root = new URL("../", import.meta.url);
const goals = JSON.parse(await readFile(new URL("goals.json", root)));
const prevWeekly = JSON.parse(await readFile(new URL("weekly_data.json", root)));

const notion = new Client({ auth: token });
const t0 = Date.now();
const { wd, warnings, stats, critical } = await rebuildWeekly({ notion, prevWeekly, goals });
console.log(`Rebuilt in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
console.log("Row counts:", JSON.stringify(stats.rows));
console.log("Headline:  ", JSON.stringify(stats.headline));
console.log(`Fill date column: ${stats.fillDateColumn} | ESF max create: ${stats.esfMaxCreate} | week Monday: ${stats.weekMonday}`);
for (const w of warnings) console.warn("WARN:", w);

// diff of the fields the rebuild actually recomputes (old -> new)
const g = (o, path) => path.split(".").reduce((v, k) => (v == null ? v : v[k]), o);
const fields = [
  "scorecard.pending_count", "scorecard.pending_total_spread", "scorecard.pending_avg_spread",
  "scorecard.net_new_starts", "scorecard.avg_start_spread", "scorecard.dumpin_count", "scorecard.dumpin_spread",
  "scorecard.lockup_spread", "scorecard.lockup_spread_goal",
  "fill_ratio.company.filled", "fill_ratio.company.openings", "fill_ratio.company.ratio",
  "meetings.quarterly_pace", "subs.weekly_avg", "hours_util.current", "hours_util.baseline",
  "raffle.current_drawing_no",
];
console.log("\nDiff (was -> now):");
for (const f of fields) {
  const a = g(prevWeekly, f), b = g(wd, f);
  const mark = JSON.stringify(a) === JSON.stringify(b) ? "   " : " * ";
  console.log(`${mark}${f}: ${JSON.stringify(a)} -> ${JSON.stringify(b)}`);
}
const rc = (o) => (o.raffle?.current_members || []).length, rn = (o) => (o.raffle?.next_up || []).length;
console.log(`   raffle.current_members: ${rc(prevWeekly)} -> ${rc(wd)} | next_up: ${rn(prevWeekly)} -> ${rn(wd)}`);
console.log("   forecast weeks:", wd.scorecard.forecast.map((f) => `${f.weekStart}:in$${f.plannedIn}(${f.inCount})/out$${f.plannedOut}`).join("  "));

if (critical) { console.error("\nCRITICAL: a required table returned 0 rows — NOT writing (would blank the dashboard)."); process.exit(2); }
if (DRY) { console.log("\n[dry-run] weekly_data.json NOT written."); process.exit(0); }

await writeFile(new URL("weekly_data.json", root), JSON.stringify(wd, null, 2) + "\n");
console.log("\nWrote weekly_data.json");
