import type { AgentKey, ToolName } from "@/agent/types";

export const TOOL_LABELS: Record<ToolName, { running: string; done: string }> = {
  get_plan_overview: { running: "Reading your plan", done: "Read your plan" },
  find_benefits: { running: "Looking up benefits", done: "Looked up benefits" },
  search_plan_document: { running: "Searching your booklet", done: "Searched your booklet" },
  get_usage: { running: "Checking what's used and left", done: "Checked what's used and left" },
  estimate_reimbursement: { running: "Estimating what the plan pays", done: "Estimated what the plan pays" },
  list_claims: { running: "Reading your claims", done: "Read your claims" },
  draft_claim: { running: "Drafting a claim", done: "Drafted a claim" },
  update_claim: { running: "Updating a claim", done: "Updated a claim" },
  ask_knowledge_agent: { running: "Asking the knowledge agent", done: "Asked the knowledge agent" },
  search_public_knowledge: { running: "Searching public reference material", done: "Searched public reference material" },
};

export function toolLabel(name: string, done: boolean): string {
  const labels = TOOL_LABELS[name as ToolName];
  if (!labels) return done ? `Ran ${name}` : `Running ${name}`;
  return done ? labels.done : labels.running;
}

export const AGENT_LABELS: Record<AgentKey, string> = {
  plan_claims: "Plan and claims agent",
  knowledge: "Knowledge agent",
};

export const PII_CATEGORY_LABELS: Record<string, string> = {
  CASocialInsuranceNumber: "Social insurance number",
  CAHealthServiceNumber: "Health card number",
  CAPersonalHealthIdentification: "Personal health number",
  CABankAccountNumber: "Bank account number",
  AUTaxFileNumber: "Tax file number",
  AUMedicalAccountNumber: "Medicare card number",
  AUBankAccountNumber: "Bank account number",
  AUDriversLicenseNumber: "Driver licence number",
  DateOfBirth: "Date of birth",
};

export function piiLabel(category: string): string {
  return PII_CATEGORY_LABELS[category] ?? category.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}
