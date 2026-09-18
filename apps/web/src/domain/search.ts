import MiniSearch, { type SearchOptions } from "minisearch";

import { indexPlan } from "./ledger";
import type { Plan, SourceRef } from "./types";

export interface BenefitHit {
  benefitId: string;
  categoryId: string;
  name: string;
  categoryName: string;
  score: number;
}

export interface DocumentHit {
  page: number;
  quote: string;
  benefitId: string | null;
  score: number;
}

interface BenefitDoc {
  id: string;
  name: string;
  keywords: string;
  itemCodes: string;
  schedule: string;
  category: string;
  categoryId: string;
  categoryName: string;
}

interface PassageDoc {
  id: string;
  text: string;
  page: number;
  benefitId: string | null;
}

const hasDigit = (term: string) => /\d/.test(term);

// Item numbers match by prefix but never fuzzily, or "505" would also return "500".
const BENEFIT_SEARCH: SearchOptions = {
  boost: { name: 3, itemCodes: 3, keywords: 2, schedule: 1.2, category: 1 },
  prefix: (term) => term.length >= 2,
  fuzzy: (term) => (hasDigit(term) || term.length < 4 ? false : 0.2),
  combineWith: "OR",
};

const STOP_WORDS = new Set(["item", "items", "number", "no", "code", "for", "the", "a", "an", "my", "of", "and", "is", "are", "do", "i", "how", "much", "what", "does"]);

const processTerm = (term: string) => {
  const t = term.toLowerCase();
  return STOP_WORDS.has(t) ? null : t;
};

const benefitIndexes = new WeakMap<Plan, MiniSearch<BenefitDoc>>();
const documentIndexes = new WeakMap<Plan, MiniSearch<PassageDoc>>();

function benefitIndex(plan: Plan): MiniSearch<BenefitDoc> {
  const cached = benefitIndexes.get(plan);
  if (cached) return cached;
  const search = new MiniSearch<BenefitDoc>({
    fields: ["name", "keywords", "itemCodes", "schedule", "category"],
    storeFields: ["name", "categoryId", "categoryName"],
    processTerm,
  });
  search.addAll(
    plan.categories.flatMap((category) =>
      category.benefits.map((b) => ({
        id: b.id,
        name: b.name,
        keywords: b.keywords.join(" "),
        itemCodes: [...b.itemCodes, ...b.coverage.scheduleItems.map((i) => i.itemCode)].join(" "),
        schedule: b.coverage.scheduleItems.map((i) => i.description).join(" "),
        category: `${category.name} ${category.kind.replace(/_/g, " ")}`,
        categoryId: category.id,
        categoryName: category.name,
      })),
    ),
  );
  benefitIndexes.set(plan, search);
  return search;
}

export function findBenefits(plan: Plan, query: string, limit = 5): BenefitHit[] {
  if (!query.trim()) return [];
  return benefitIndex(plan)
    .search(query, BENEFIT_SEARCH)
    .slice(0, limit)
    .map((hit) => ({
      benefitId: String(hit.id),
      categoryId: String(hit.categoryId),
      name: String(hit.name),
      categoryName: String(hit.categoryName),
      score: hit.score,
    }));
}

function documentIndex(plan: Plan): MiniSearch<PassageDoc> {
  const cached = documentIndexes.get(plan);
  if (cached) return cached;
  const passages = new Map<string, PassageDoc>();
  const add = (source: SourceRef | null | undefined, text: string | null | undefined, benefitId: string | null) => {
    if (!source || !text?.trim()) return;
    const id = `${source.page}:${text.trim()}`;
    if (!passages.has(id)) passages.set(id, { id, text: text.trim(), page: source.page, benefitId });
  };
  for (const benefit of indexPlan(plan).benefits.values()) {
    add(benefit.source, benefit.source?.quote, benefit.id);
    add(benefit.source, benefit.notes, benefit.id);
    add(benefit.source, benefit.coverage.scheduleNote, benefit.id);
    add(benefit.source, benefit.waitingPeriod?.note, benefit.id);
    for (const requirement of benefit.requirements) add(benefit.source, requirement, benefit.id);
  }
  for (const pool of plan.limitPools) add(pool.source, pool.source?.quote, null);
  for (const share of plan.costShares) add(share.source, share.source?.quote, null);
  const rules = plan.claimRules;
  add(rules.source, rules.source?.quote, null);
  for (const note of rules.notes) add(rules.source, note, null);
  if (plan.profile.kind === "AU") {
    for (const category of plan.profile.hospital?.categories ?? []) add(category.source, category.source?.quote, null);
  }
  const search = new MiniSearch<PassageDoc>({
    fields: ["text"],
    storeFields: ["text", "page", "benefitId"],
    processTerm,
  });
  search.addAll([...passages.values()]);
  documentIndexes.set(plan, search);
  return search;
}

export function searchPlanDocument(plan: Plan, query: string, limit = 5): DocumentHit[] {
  if (!query.trim()) return [];
  return documentIndex(plan)
    .search(query, {
      prefix: (term) => term.length >= 3,
      fuzzy: (term) => (hasDigit(term) || term.length < 5 ? false : 0.2),
      combineWith: "OR",
    })
    .slice(0, limit)
    .map((hit) => ({
      page: Number(hit.page),
      quote: String(hit.text),
      benefitId: (hit.benefitId as string | null) ?? null,
      score: hit.score,
    }));
}
