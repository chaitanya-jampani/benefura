// Every API request goes through here and is logged so the privacy inspector can show exactly what left the browser.
import { db, getSetting, SETTING_KEYS, type OutboundKind } from "@/db/dexie";
import type {
  AnalyzeChunkResponse,
  AssembleRequest,
  AssembleResponse,
  ErrorCode,
  ErrorResponse,
  HealthResponse,
  PiiDetectedDetail,
  ReceiptAnalyzeResponse,
  Region,
} from "@/domain/types";

export const TRACE_HEADER = "x-benefura-trace-id";

const DEFAULT_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function apiBaseUrl(): Promise<string> {
  const override = await getSetting<string | null>(SETTING_KEYS.apiBaseUrl, null);
  return (override || DEFAULT_BASE).replace(/\/+$/, "");
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode | "network_error" | "unknown",
    message: string,
    readonly details: PiiDetectedDetail[] | null = null,
    readonly retryAfterSeconds: number | null = null,
    readonly traceId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface OutboundMeta {
  kind: OutboundKind;
  thumbnails?: string[];
  preview?: string;
}

function bodySize(body: BodyInit | null | undefined): number {
  if (!body) return 0;
  if (typeof body === "string") return new TextEncoder().encode(body).byteLength;
  if (body instanceof Blob) return body.size;
  if (body instanceof ArrayBuffer) return body.byteLength;
  if (ArrayBuffer.isView(body)) return body.byteLength;
  if (body instanceof FormData) {
    let n = 0;
    body.forEach((v) => {
      n += typeof v === "string" ? new TextEncoder().encode(v).byteLength : v.size;
    });
    return n;
  }
  return 0;
}

export async function loggedFetch(url: string, init: RequestInit, meta: OutboundMeta): Promise<Response> {
  const started = performance.now();
  const id = await db.outbound.add({
    at: new Date().toISOString(),
    kind: meta.kind,
    method: init.method ?? "GET",
    url,
    bytes: bodySize(init.body),
    thumbnails: meta.thumbnails ?? [],
    preview: meta.preview?.slice(0, 2000),
  });
  try {
    const res = await fetch(url, init);
    await db.outbound.update(id, {
      status: res.status,
      traceId: res.headers.get(TRACE_HEADER) ?? undefined,
      durationMs: Math.round(performance.now() - started),
    });
    return res;
  } catch (err) {
    await db.outbound.update(id, { status: 0, durationMs: Math.round(performance.now() - started) });
    throw new ApiError(0, "network_error", err instanceof Error ? err.message : "Network error");
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  const traceId = res.headers.get(TRACE_HEADER);
  const retryHeader = res.headers.get("retry-after");
  try {
    const body = (await res.json()) as ErrorResponse;
    const e = body.error;
    return new ApiError(
      res.status,
      e.code,
      e.message,
      e.details ?? null,
      e.retryAfterSeconds ?? (retryHeader ? Number(retryHeader) : null),
      e.traceId ?? traceId,
    );
  } catch {
    return new ApiError(res.status, "unknown", res.statusText || "Request failed", null, null, traceId);
  }
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function withBackoff<T>(fn: () => Promise<T>, { retries = 4, baseMs = 1500 } = {}): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fn();
    } catch (err) {
      const retryable =
        err instanceof ApiError &&
        (err.code === "rate_limited" || err.code === "network_error" || err.status === 502 || err.status === 504);
      if (!retryable || attempt >= retries) throw err;
      const hinted = err.retryAfterSeconds ? err.retryAfterSeconds * 1000 : 0;
      const backoff = baseMs * 2 ** attempt + Math.random() * 400;
      await sleep(Math.max(hinted, backoff));
    }
  }
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const base = await apiBaseUrl();
  const res = await loggedFetch(`${base}/healthz`, { method: "GET", signal }, { kind: "healthz" });
  return parse<HealthResponse>(res);
}

export async function analyzeChunk(
  input: { documentId: string; region: Region; pages: number[]; pdf: Blob; thumbnails: string[] },
  signal?: AbortSignal,
): Promise<AnalyzeChunkResponse> {
  const base = await apiBaseUrl();
  const form = new FormData();
  form.set("documentId", input.documentId);
  form.set("region", input.region);
  form.set("pages", input.pages.join(","));
  form.set("file", input.pdf, `pages-${input.pages[0]}-${input.pages[input.pages.length - 1]}.pdf`);
  const res = await loggedFetch(
    `${base}/api/plan/analyze-chunk`,
    { method: "POST", body: form, signal },
    { kind: "analyze-chunk", thumbnails: input.thumbnails },
  );
  return parse<AnalyzeChunkResponse>(res);
}

export async function assemblePlan(body: AssembleRequest, signal?: AbortSignal): Promise<AssembleResponse> {
  const base = await apiBaseUrl();
  const json = JSON.stringify(body);
  const res = await loggedFetch(
    `${base}/api/plan/assemble`,
    { method: "POST", body: json, headers: { "content-type": "application/json" }, signal },
    { kind: "assemble", preview: `${body.chunks.length} chunks of extracted rows` },
  );
  return parse<AssembleResponse>(res);
}

export async function analyzeReceipt(
  input: { region: Region; image: Blob; filename?: string; thumbnail: string },
  signal?: AbortSignal,
): Promise<ReceiptAnalyzeResponse> {
  const base = await apiBaseUrl();
  const form = new FormData();
  form.set("region", input.region);
  form.set("file", input.image, input.filename ?? "receipt.jpg");
  const res = await loggedFetch(
    `${base}/api/receipts/analyze`,
    { method: "POST", body: form, signal },
    { kind: "receipt", thumbnails: [input.thumbnail] },
  );
  return parse<ReceiptAnalyzeResponse>(res);
}

export function chatFetch(previewOf: (body: string) => string): typeof fetch {
  return async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const body = typeof init?.body === "string" ? init.body : "";
    return loggedFetch(url, init ?? {}, { kind: "chat", preview: previewOf(body) });
  };
}

export async function chatUrl(): Promise<string> {
  return `${await apiBaseUrl()}/api/chat`;
}
