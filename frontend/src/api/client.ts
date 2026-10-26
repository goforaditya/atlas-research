import type {
  ApiErrorBody,
  CreateRunRequest,
  CreateRunResponse,
  EvidenceResponse,
  ReplayInfo,
  ResearchRunState,
  RunCompletedPayload,
  RunErrorPayload,
  RunStartedPayload,
  StageEvent,
} from "../types/atlas";

const API = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message);
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: ApiErrorBody = { error_code: "HTTP_ERROR", message: res.statusText };
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      /* keep default */
    }
    throw new ApiError(res.status, body);
  }
  return (await res.json()) as T;
}

export interface Health {
  status: string;
  sqlite_wal: boolean;
  golden_runs_loaded: string[];
  llm_configured: boolean;
  version: string;
}

export async function fetchHealth(): Promise<Health> {
  return json<Health>(await fetch(`${API}/health`));
}

export async function fetchReplays(): Promise<ReplayInfo[]> {
  return (await json<{ replays: ReplayInfo[] }>(await fetch(`${API}/replays`))).replays;
}

export async function createRun(req: CreateRunRequest): Promise<CreateRunResponse> {
  return json<CreateRunResponse>(
    await fetch(`${API}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) }),
  );
}

export async function fetchRunState(runId: string): Promise<ResearchRunState> {
  return json<ResearchRunState>(await fetch(`${API}/runs/${runId}`));
}

export async function fetchEvidence(runId: string, evidenceId: string): Promise<EvidenceResponse> {
  return json<EvidenceResponse>(await fetch(`${API}/runs/${runId}/evidence/${encodeURIComponent(evidenceId)}`));
}

export function exportUrl(runId: string, format: "md" | "pdf"): string {
  return `${API}/runs/${runId}/export?format=${format}`;
}

export interface StreamHandlers {
  onStarted?: (p: RunStartedPayload) => void;
  onEvent: (e: StageEvent) => void;
  onCompleted?: (p: RunCompletedPayload) => void;
  onError?: (p: RunErrorPayload | { message: string }) => void;
}

/** The one EventSource listener used by Live and Replay runs alike (Section 10.3). Returns a close function.
 *  The browser resends Last-Event-ID on reconnect, so the server resumes after the last event received. */
export function subscribe(streamUrl: string, h: StreamHandlers): () => void {
  const es = new EventSource(streamUrl);
  let finished = false;
  es.addEventListener("run_started", (m) => h.onStarted?.(JSON.parse((m as MessageEvent).data)));
  es.addEventListener("stage_event", (m) => h.onEvent(JSON.parse((m as MessageEvent).data) as StageEvent));
  es.addEventListener("run_completed", (m) => {
    finished = true;
    es.close();
    h.onCompleted?.(JSON.parse((m as MessageEvent).data));
  });
  es.addEventListener("run_error", (m) => {
    finished = true;
    es.close();
    h.onError?.(JSON.parse((m as MessageEvent).data));
  });
  es.onerror = () => {
    if (!finished && es.readyState === EventSource.CLOSED) h.onError?.({ message: "Stream closed unexpectedly" });
  };
  return () => {
    finished = true;
    es.close();
  };
}
