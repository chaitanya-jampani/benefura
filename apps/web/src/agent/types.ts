// Mirrors apps/api/app/chat: tool_registry.py, server_tools.py and route.py.
import type { UIMessage } from "ai";

export type AgentKey = "plan_claims" | "knowledge";

/** Sent in the stream's `start`/`finish` chunks and merged into `message.metadata`. */
export interface ChatMessageMetadata {
  agent?: AgentKey | null;
  agentName?: string | null;
  agentVersion?: string | null;
  traceId?: string;
  route?: "plan_claims" | "knowledge" | "off_topic" | null;
  /** The API refused this turn, so it is never replayed to a model. */
  blocked?: "pii" | "content_filter";
}

export interface Citation {
  sourceId: string;
  title: string;
  url: string;
  publisher: string;
  license: string;
  attribution: string;
  section?: string | null;
  region?: string;
}

// A type alias (not an interface) so it satisfies the SDK's `Record<string, unknown>` constraint.
export type ChatDataParts = {
  status: { message: string; state: "running" | "done" };
  citations: { citations: Citation[] };
  "pii-warning": { categories: string[]; message: string };
};

export type ClaimStatusInput = "draft" | "submitted" | "paid" | "partially_paid" | "rejected";

export interface DraftClaimLineInput {
  benefit_id: string;
  service_date: string;
  charged_cents: number;
  quantity: number | null;
  item_code: string | null;
  description: string | null;
  other_plan_paid_cents?: number | null;
}

export interface ToolInputs {
  get_plan_overview: Record<string, never>;
  find_benefits: { query: string };
  search_plan_document: { query: string };
  get_usage: { benefit_id: string | null; member_alias: string | null };
  estimate_reimbursement: {
    benefit_id: string;
    member_alias: string;
    service_date: string;
    charged_cents: number;
    quantity: number | null;
    item_code: string | null;
    other_plan_paid_cents?: number | null;
  };
  list_claims: { status: ClaimStatusInput | null; benefit_id: string | null };
  draft_claim: { member_alias: string; provider: string | null; lines: DraftClaimLineInput[] };
  update_claim: {
    claim_id: string;
    status: ClaimStatusInput | null;
    paid_cents: number | null;
    provider: string | null;
    note: string | null;
  };
  ask_knowledge_agent: { question: string; region: "CA" | "AU" };
  search_public_knowledge: { query: string; region: "CA" | "AU"; top_k: number };
}

export type BrowserToolName =
  | "get_plan_overview"
  | "find_benefits"
  | "search_plan_document"
  | "get_usage"
  | "estimate_reimbursement"
  | "list_claims"
  | "draft_claim"
  | "update_claim";

export type ServerToolName = "ask_knowledge_agent" | "search_public_knowledge";
export type ToolName = BrowserToolName | ServerToolName;

export type ChatTools = {
  [K in ToolName]: { input: ToolInputs[K]; output: unknown };
};

export type BenefuraUIMessage = UIMessage<ChatMessageMetadata, ChatDataParts, ChatTools>;
export type BenefuraPart = BenefuraUIMessage["parts"][number];
