// Nothing here is sent in bulk: the API only receives redacted images, aliased chat text and tool outputs.
import Dexie, { type EntityTable } from "dexie";
import type { UIMessage } from "ai";

import type {
  AnalyzeChunkResponse,
  Claim,
  FieldConfidence,
  Issue,
  Plan,
  Receipt,
  Region,
} from "@/domain/types";

export interface PlanRecord {
  id: string;
  plan: Plan;
  isDemo: boolean;
  createdAt: string;
  updatedAt: string;
}

export type ClaimRecord = Claim;

export interface ReceiptRecord {
  id: string;
  planId: string;
  claimId?: string;
  /** Redacted image; originals are never persisted. */
  image?: Blob;
  mime?: string;
  width?: number;
  height?: number;
  receipt?: Receipt;
  fieldConfidence?: Record<string, FieldConfidence>;
  issues: Issue[];
  traceId?: string;
  source: "upload" | "demo" | "manual";
  createdAt: string;
}

export type AliasKind =
  | "member"
  | "employer"
  | "policy"
  | "address"
  | "phone"
  | "email"
  | "identifier"
  | "custom";

export interface AliasRecord {
  id: string;
  kind: AliasKind;
  token: string;
  /** Raw values; never leave the browser. */
  values: string[];
  relationship?: "self" | "spouse" | "child" | "dependent";
  createdAt: string;
}

export type DocumentStatus = "redacting" | "acknowledged" | "extracting" | "review" | "done";

export interface DocumentRecord {
  id: string;
  region: Region;
  name: string;
  pageCount: number;
  status: DocumentStatus;
  isSample: boolean;
  acknowledgedAt?: string;
  planId?: string;
  createdAt: string;
  updatedAt: string;
}

/** x, y, w, h are fractions (0..1) of the page width and height. */
export interface RedactionBox {
  id: string;
  pageIndex: number;
  x: number;
  y: number;
  w: number;
  h: number;
  token: string | null;
  kind: AliasKind;
  source: "detector" | "manual" | "server_suggestion";
  detector?: string;
  confidence: number;
  enabled: boolean;
}

export interface PageRecord {
  /** `${documentId}:${pageIndex}` */
  id: string;
  documentId: string;
  pageIndex: number;
  widthPx: number;
  heightPx: number;
  dpi: number;
  /** Redactions are burned into the raster; there is no text layer. */
  image?: Blob;
  boxes: RedactionBox[];
  textSource: "pdf" | "ocr" | "none";
}

export interface ExtractionRecord {
  /** `${documentId}:${chunkIndex}` */
  id: string;
  documentId: string;
  chunkIndex: number;
  pages: number[];
  status: "pending" | "running" | "done" | "error";
  response?: AnalyzeChunkResponse;
  error?: { code: string; message: string; status?: number };
  updatedAt: string;
}

export interface ChatRecord {
  id: string;
  planId?: string;
  messages: UIMessage[];
  activeAgent?: "plan_claims" | "knowledge";
  createdAt: string;
  updatedAt: string;
}

export type OutboundKind = "healthz" | "analyze-chunk" | "assemble" | "receipt" | "chat";

export interface OutboundRecord {
  id?: number;
  at: string;
  kind: OutboundKind;
  method: string;
  url: string;
  bytes: number;
  thumbnails: string[];
  /** Already aliased. */
  preview?: string;
  status?: number;
  traceId?: string;
  durationMs?: number;
}

export interface SettingRecord {
  key: string;
  value: unknown;
}

export class BenefuraDB extends Dexie {
  plans!: EntityTable<PlanRecord, "id">;
  claims!: EntityTable<ClaimRecord, "id">;
  receipts!: EntityTable<ReceiptRecord, "id">;
  aliases!: EntityTable<AliasRecord, "id">;
  documents!: EntityTable<DocumentRecord, "id">;
  pages!: EntityTable<PageRecord, "id">;
  extractions!: EntityTable<ExtractionRecord, "id">;
  chats!: EntityTable<ChatRecord, "id">;
  outbound!: EntityTable<OutboundRecord, "id">;
  settings!: EntityTable<SettingRecord, "key">;

  constructor(name = "benefura") {
    super(name);
    this.version(1).stores({
      plans: "id, updatedAt",
      claims: "id, planId, status, updatedAt",
      receipts: "id, planId, claimId, createdAt",
      aliases: "id, kind, token",
      documents: "id, status, updatedAt",
      pages: "id, documentId, [documentId+pageIndex]",
      extractions: "id, documentId, [documentId+chunkIndex]",
      chats: "id, planId, updatedAt",
      outbound: "++id, at, kind",
      settings: "key",
    });
  }
}

export const db = new BenefuraDB();

export const SETTING_KEYS = {
  activePlanId: "activePlanId",
  includeSubmittedClaims: "includeSubmittedClaims",
  apiBaseUrl: "apiBaseUrl",
  rasterDpi: "rasterDpi",
} as const;

export async function getSetting<T>(key: string, fallback: T): Promise<T> {
  const row = await db.settings.get(key);
  return row === undefined ? fallback : (row.value as T);
}

export async function setSetting(key: string, value: unknown): Promise<void> {
  await db.settings.put({ key, value });
}
