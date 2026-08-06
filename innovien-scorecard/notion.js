// Thin Notion query layer. Uses the official @notionhq/client.
// Each database is single-source, so the classic /databases/{id}/query works.
import { Client } from "@notionhq/client";

export function getClient() {
  const token = process.env.NOTION_TOKEN;
  if (!token) throw new Error("NOTION_TOKEN is not set. Add it in Vercel project settings (Environment Variables).");
  return new Client({ auth: token });
}

export const DB = {
  activeContracts: process.env.DB_ACTIVE_CONTRACTS || "644d8fc2-c481-42bf-8763-74ea84fcd389",
  recruiterDaily:  process.env.DB_RECRUITER_DAILY  || "9a031270-90b0-4db1-a691-2ff7bea1a169",
  amWeekly:        process.env.DB_AM_WEEKLY        || "b5a62657-c3ec-4592-a69f-a1cf3a64081d",
  openReqs:        process.env.DB_OPEN_REQS        || "808a92ee-9125-4a9b-b45b-0d7192db4449",
  placementEvents: process.env.DB_PLACEMENT_EVENTS || "132c6ee8-fb8c-4dda-a83b-80b0b1373a7b",
  innovienNext:    process.env.DB_INNOVIEN_NEXT    || "c420fa85-b8df-8321-925d-01073f86f699",
  esfPipeline:     process.env.DB_ESF_PIPELINE     || "6981fee2-c458-4e09-99ed-a29fe9e4633d",
  psfPipeline:     process.env.DB_PSF_PIPELINE     || "08344154-4a4e-47a4-b7ae-f0e322caf834",
  companyGoals:    process.env.DB_COMPANY_GOALS    || "68691cd3-0222-4760-b2ed-8bd07ce528ae",
  // People roster (Comtrak-fed). Used to keep the Q2 tab to ACTIVE AMs/Recruiters only.
  people:          process.env.DB_PEOPLE          || "3600fa85-b8df-80d9-be56-d5f97893a7ab",
};

// Query every page in a database (handles pagination + an optional filter).
export async function queryAll(notion, database_id, filter) {
  const rows = [];
  let cursor;
  do {
    const res = await notion.databases.query({
      database_id,
      start_cursor: cursor,
      page_size: 100,
      ...(filter ? { filter } : {}),
    });
    rows.push(...res.results);
    cursor = res.has_more ? res.next_cursor : undefined;
  } while (cursor);
  return rows;
}

// --- property readers (defensive against missing/renamed props) ---
export const P = {
  num:  (pg, name) => pg.properties?.[name]?.number ?? null,
  sel:  (pg, name) => pg.properties?.[name]?.select?.name ?? null,
  date: (pg, name) => pg.properties?.[name]?.date?.start ?? null,
  text: (pg, name) => {
    const p = pg.properties?.[name];
    if (!p) return null;
    const arr = p.rich_text || p.title;
    if (Array.isArray(arr)) return arr.map(t => t.plain_text).join("") || null;
    return null;
  },
  formulaNum: (pg, name) => pg.properties?.[name]?.formula?.number ?? null,
  // Resolved formula value regardless of result type (boolean / string / number / date).
  formula: (pg, name) => {
    const f = pg.properties?.[name]?.formula;
    if (!f) return null;
    if ("boolean" in f && f.boolean !== null && f.boolean !== undefined) return f.boolean;
    if ("string" in f && f.string != null) return f.string;
    if ("number" in f && f.number != null) return f.number;
    if ("date" in f && f.date) return f.date.start ?? null;
    return null;
  },
};
