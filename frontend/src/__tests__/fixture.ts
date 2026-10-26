import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { resolve } from "node:path";
import type { StageEvent } from "../types/atlas";

/** Loads the committed synthetic replay so UI tests run against the same events the server streams. */
export function loadSyntheticEvents(): StageEvent[] {
  // Vitest runs from frontend/, so the repo data directory is one level up.
  const path = resolve(process.cwd(), "../data/golden_runs/sample_synthetic.jsonl.gz");
  const lines = gunzipSync(readFileSync(path)).toString("utf8").split("\n").filter(Boolean);
  return lines.slice(1).map((l) => JSON.parse(l) as StageEvent);
}
