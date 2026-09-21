"use client";

import { EyeOff, FileWarning, ListChecks, UserRound } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { buttonStyles, SectionTitle } from "@/components/ui";
import type { Issue } from "@/domain/types";
import { categoryPhrase, fixHref } from "@/redaction/categories";

import { issueGroup, type IssueGroup, type RowSource } from "./plan-edit";

const GROUPS: Record<IssueGroup, { title: string; intro?: string; icon: ReactNode }> = {
  personal: {
    title: "Details the server noticed",
    intro:
      "Names, addresses and phone numbers are often the insurer's own. If any of these are yours, fix the page and send it again.",
    icon: <UserRound className="size-5" strokeWidth={1.5} />,
  },
  excluded: {
    title: "Pages left out",
    intro: "These pages weren't used for the plan.",
    icon: <EyeOff className="size-5" strokeWidth={1.5} />,
  },
  rows: {
    title: "Rows to double-check",
    intro: "The checker couldn't fully confirm these against the page. Compare them with the table above.",
    icon: <ListChecks className="size-5" strokeWidth={1.5} />,
  },
  other: {
    title: "Other notes",
    icon: <FileWarning className="size-5" strokeWidth={1.5} />,
  },
};

function describe(issue: Issue): string {
  if (issue.code === "pii_advisory") return `Mentions ${categoryPhrase(issue.category)}`;
  if (issue.code === "prompt_injection") return "Contained text that looked like instructions to the AI, so it was left out";
  return issue.message;
}

export function IssuesPanel({ issues, sources, documentId }: { issues: readonly Issue[]; sources: ReadonlyMap<string, RowSource>; documentId: string }) {
  if (issues.length === 0) return null;
  const grouped = new Map<IssueGroup, Issue[]>();
  for (const issue of issues) {
    const g = issueGroup(issue);
    grouped.set(g, [...(grouped.get(g) ?? []), issue]);
  }
  const order: IssueGroup[] = ["personal", "excluded", "rows", "other"];

  return (
    <div className="flex flex-col gap-8">
      <SectionTitle as="h2">Issues</SectionTitle>
      {order
        .filter((g) => grouped.has(g))
        .map((g) => {
          const meta = GROUPS[g];
          const list = grouped.get(g)!;
          const body = (
            <ul className="flex flex-col gap-3">
              {list.map((issue, i) => {
                const source = issue.rowId ? sources.get(issue.rowId) : undefined;
                const page = issue.page ?? source?.page;
                return (
                  <li key={`${issue.code}-${issue.page}-${issue.rowId}-${i}`} className="flex flex-col gap-2 rounded-tile bg-sunken/70 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex min-w-0 flex-col gap-1">
                      <p className="text-base text-ink">
                        {page ? <span className="text-muted">Page {page} · </span> : null}
                        {describe(issue)}
                      </p>
                      {source?.quote && (
                        <p className="text-sm text-ink-2">
                          <q className="italic">{source.quote}</q>
                        </p>
                      )}
                    </div>
                    {g === "personal" && page && (
                      <Link href={fixHref(documentId, { page, category: issue.category ?? "", polygon: null }, page)} className={buttonStyles({ size: "sm", className: "shrink-0 self-start" })}>
                        Fix page {page}
                      </Link>
                    )}
                  </li>
                );
              })}
            </ul>
          );
          return (
            <section key={g} className="flex flex-col gap-3" aria-label={meta.title}>
              {g === "other" ? (
                <details className="group">
                  <summary className="flex cursor-pointer list-none items-center gap-3 rounded-tile py-1 text-lg font-medium text-ink">
                    <span aria-hidden className="grid size-11 place-items-center rounded-tile bg-surface shadow-tile">
                      {meta.icon}
                    </span>
                    {meta.title} ({list.length})
                    <span className="text-sm font-normal text-muted group-open:hidden">Show</span>
                  </summary>
                  <div className="mt-3">{body}</div>
                </details>
              ) : (
                <>
                  <h3 className="flex items-center gap-3 text-lg font-medium text-ink">
                    <span aria-hidden className="grid size-11 place-items-center rounded-tile bg-surface shadow-tile">
                      {meta.icon}
                    </span>
                    {meta.title}
                  </h3>
                  {meta.intro && <p className="text-base text-muted">{meta.intro}</p>}
                  {body}
                </>
              )}
            </section>
          );
        })}
    </div>
  );
}
