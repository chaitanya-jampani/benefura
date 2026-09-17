// Shapes are generated from apps/api/app/models (`pnpm gen:types`); don't hand-edit them here.
import type { components } from "@/api/schema";

type S = components["schemas"];

export type Plan = S["Plan"];
export type Region = Plan["region"];
export type Currency = Plan["currency"];
export type Period = S["Period"];
export type PeriodKind = Period["kind"];
export type Coverage = S["Coverage"];
export type Limit = S["Limit"];
export type Frequency = S["Frequency"];
export type WaitingPeriod = S["WaitingPeriod"];
export type Benefit = S["Benefit"];
export type Category = S["Category"];
export type LimitPool = S["LimitPool"];
export type CostShare = S["CostShare"];
export type ClaimRules = S["ClaimRules"];
export type Member = S["Member"];
export type CAProfile = S["CAProfile"];
export type AUProfile = S["AUProfile"];
export type HospitalCover = S["HospitalCover"];
export type SourceRef = S["SourceRef"];

export type Claim = S["Claim"];
export type ClaimStatus = Claim["status"];
export type ClaimLine = S["ClaimLine"];
export type ClaimHistoryEntry = S["ClaimHistoryEntry"];

export type Receipt = S["Receipt"];
export type ReceiptLine = S["ReceiptLine"];
export type FieldConfidence = S["FieldConfidence"];

export type Issue = S["Issue"];
export type Usage = S["Usage"];
export type HealthResponse = S["HealthResponse"];
export type AnalyzeChunkResponse = S["AnalyzeChunkResponse"];
export type ExtractedRows = S["ExtractedRows"];
export type AssembleRequest = S["AssembleRequest"];
export type AssembleResponse = S["AssembleResponse"];
export type ReceiptAnalyzeResponse = S["ReceiptAnalyzeResponse"];
export type ChatContext = S["ChatContext"];
export type ChatRequest = S["ChatRequest"];
export type ErrorResponse = S["ErrorResponse"];
export type ErrorCode = S["ErrorBody"]["code"];
export type PiiDetectedDetail = S["PiiDetectedDetail"];

/** ISO local date, YYYY-MM-DD. */
export type IsoDate = string;
