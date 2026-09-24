import { ExternalLink } from "lucide-react";

import type { Citation } from "./types";

export function uniqueCitations(groups: Citation[][]): Citation[] {
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const citation of groups.flat()) {
    if (seen.has(citation.sourceId)) continue;
    seen.add(citation.sourceId);
    out.push(citation);
  }
  return out;
}

export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <section aria-label="Sources" className="flex w-full max-w-xl flex-col gap-2" data-testid="citations">
      <h3 className="text-sm font-medium text-ink-2">Sources</h3>
      <ol className="flex flex-col gap-2">
        {citations.map((citation, i) => (
          <li key={citation.sourceId} className="flex gap-3 rounded-tile bg-surface p-3 shadow-tile" data-testid="citation">
            <span aria-hidden className="grid size-6 shrink-0 place-items-center rounded-full bg-sunken text-xs text-ink-2 tabular-nums">
              {i + 1}
            </span>
            <div className="flex min-w-0 flex-col gap-1">
              <a
                href={citation.url}
                target="_blank"
                rel="noopener noreferrer"
                className="group inline-flex items-start gap-1.5 rounded-sm font-medium text-ink underline decoration-faint underline-offset-4 hover:decoration-ink"
              >
                <span className="min-w-0 break-words">{citation.title}</span>
                <ExternalLink aria-hidden className="mt-1 size-3.5 shrink-0 text-muted" strokeWidth={1.5} />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
              <p className="text-sm text-ink-2">
                {citation.publisher}
                <span aria-hidden> · </span>
                <span className="sr-only">, licence: </span>
                {citation.license}
              </p>
              {citation.attribution && <p className="text-xs text-muted">{citation.attribution}</p>}
              {citation.section && <p className="text-xs text-muted">{citation.section}</p>}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
