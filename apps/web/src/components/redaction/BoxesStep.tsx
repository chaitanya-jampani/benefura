"use client";

import { ChevronLeft, ChevronRight, Search, SquareDashedMousePointer, Trash } from "lucide-react";
import { useId, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { Button, SectionTitle, Surface } from "@/components/ui";
import type { AliasKind, RedactionBox } from "@/db/dexie";
import { cn } from "@/lib/cn";
import { compareTokens, FAMILY_KIND, newId, nextFreeToken, normalizeAliasValue, parseToken } from "@/redaction/apply";
import { findValueOnPage } from "@/redaction/detectors";
import type { RedactionSession } from "@/redaction/session";
import type { BoxCandidate, Rect, TokenFamily } from "@/redaction/types";

import { PageCanvas } from "./PageCanvas";
import { Switch, TextInput, TOKEN_FONT } from "./parts";

const NEW_OPTIONS: ReadonlyArray<{ family: TokenFamily; label: string }> = [
  { family: "MEMBER", label: "New label for a person" },
  { family: "EMPLOYER", label: "New label for an organization" },
  { family: "ID", label: "New label for a number" },
  { family: "ADDRESS", label: "New label for an address" },
];

function kindForToken(token: string | null): AliasKind {
  const family = token ? parseToken(token)?.family : undefined;
  return family ? FAMILY_KIND[family] : "custom";
}

const selectClass =
  "h-9 max-w-[8.5rem] shrink-0 cursor-pointer appearance-none rounded-full bg-surface px-3 text-xs font-semibold tracking-tight text-ink ring-1 ring-ink/70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

export function TokenSelect({
  value,
  used,
  onChange,
  label,
  disabled,
  className,
}: {
  value: string | null;
  used: readonly string[];
  onChange: (token: string | null) => void;
  label: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <select
      aria-label={label}
      value={value ?? ""}
      disabled={disabled}
      style={TOKEN_FONT}
      className={cn(selectClass, !value && "bg-ink text-white ring-ink", className)}
      onChange={(e) => {
        const v = e.target.value;
        if (v.startsWith("new:")) onChange(nextFreeToken(v.slice(4) as TokenFamily, used));
        else onChange(v || null);
      }}
    >
      <option value="">Black box</option>
      {used.map((t) => (
        <option key={t} value={t}>
          {t}
        </option>
      ))}
      {NEW_OPTIONS.map((o) => (
        <option key={o.family} value={`new:${o.family}`}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function BoxesStep({
  session,
  candidates,
  onCandidates,
  pageIndex,
  onPageIndex,
  selectedId,
  onSelect,
  lockedIds,
  savedTokens,
  notice,
  onBack,
  onContinue,
}: {
  session: RedactionSession;
  candidates: readonly BoxCandidate[];
  onCandidates: (next: BoxCandidate[]) => void;
  pageIndex: number;
  onPageIndex: (index: number) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  lockedIds: ReadonlySet<string>;
  savedTokens: readonly string[];
  notice?: ReactNode;
  onBack?: () => void;
  onContinue: () => void;
}) {
  const [drawMode, setDrawMode] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [findValue, setFindValue] = useState("");
  const [findToken, setFindToken] = useState<string | null>(null);
  const ids = useId();

  const page = session.pages[pageIndex];
  const pageCount = session.pages.length;
  const onPage = useMemo(() => candidates.filter((c) => c.box.pageIndex === pageIndex), [candidates, pageIndex]);
  const used = useMemo(() => {
    const set = new Set<string>(savedTokens);
    for (const c of candidates) if (c.box.token) set.add(c.box.token);
    return [...set].sort(compareTokens);
  }, [candidates, savedTokens]);
  const perPage = useMemo(() => {
    const counts = new Array<number>(pageCount).fill(0);
    for (const c of candidates) if (c.box.enabled) counts[c.box.pageIndex]++;
    return counts;
  }, [candidates, pageCount]);
  const enabledCount = perPage.reduce((a, b) => a + b, 0);
  const pagesWithBoxes = perPage.filter(Boolean).length;

  const update = (id: string, patch: Partial<RedactionBox>) =>
    onCandidates(candidates.map((c) => (c.box.id === id ? { ...c, box: { ...c.box, ...patch } } : c)));

  const changeToken = (target: BoxCandidate, token: string | null) => {
    const key = normalizeAliasValue(target.value);
    const matches = (c: BoxCandidate) =>
      c.box.id === target.box.id || (key !== "" && c.box.token === target.box.token && normalizeAliasValue(c.value) === key);
    const kind = kindForToken(token);
    let n = 0;
    const next = candidates.map((c) => {
      if (!matches(c) || lockedIds.has(c.box.id)) return c;
      n++;
      return { ...c, box: { ...c.box, token, kind: token ? kind : c.box.kind } };
    });
    onCandidates(next);
    setAnnouncement(n > 1 ? `Updated ${n} boxes with the same value` : "Label updated");
  };

  const draw = (rect: Rect) => {
    const box: RedactionBox = {
      id: newId(),
      pageIndex,
      ...rect,
      token: null,
      kind: "custom",
      source: "manual",
      detector: "drawn",
      confidence: 1,
      enabled: true,
    };
    onCandidates([...candidates, { box, value: "", label: "Box you drew" }]);
    onSelect(box.id);
    setDrawMode(false);
    setAnnouncement("Box added. Use arrow keys on the page to move it.");
  };

  const findAll = (e: FormEvent) => {
    e.preventDefault();
    const value = findValue.trim();
    if (!value) return;
    const found: BoxCandidate[] = [];
    for (const p of session.pages) {
      if (!p.text || p.preRedacted) continue;
      for (const hit of findValueOnPage(p.text, value, findToken, kindForToken(findToken))) {
        const duplicate = candidates.some(
          (c) =>
            c.box.pageIndex === hit.box.pageIndex &&
            c.box.enabled &&
            Math.abs(c.box.x - hit.box.x) < 0.004 &&
            Math.abs(c.box.y - hit.box.y) < 0.004 &&
            Math.abs(c.box.w - hit.box.w) < 0.01,
        );
        if (!duplicate) found.push(hit);
      }
    }
    if (found.length === 0) {
      setAnnouncement(`No new matches for “${value}”. If it's in a picture or a scan the text reader missed, draw a box instead.`);
      return;
    }
    onCandidates([...candidates, ...found]);
    const pages = new Set(found.map((f) => f.box.pageIndex));
    setAnnouncement(`Added ${found.length} ${found.length === 1 ? "box" : "boxes"} on ${pages.size} ${pages.size === 1 ? "page" : "pages"}`);
    if (!pages.has(pageIndex)) onPageIndex(Math.min(...pages));
    setFindValue("");
  };

  return (
    <div className="flex flex-col gap-6">
      {notice}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_24rem] lg:items-start">
        <Surface className="flex flex-col gap-4 p-4 sm:p-6 lg:sticky lg:top-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                aria-label="Previous page"
                disabled={pageIndex === 0}
                onClick={() => {
                  onSelect(null);
                  onPageIndex(pageIndex - 1);
                }}
                className="size-9 px-0"
              >
                <ChevronLeft aria-hidden className="size-5" strokeWidth={1.5} />
              </Button>
              <label htmlFor={`${ids}-page`} className="sr-only">
                Page
              </label>
              <select
                id={`${ids}-page`}
                value={pageIndex}
                onChange={(e) => {
                  onSelect(null);
                  onPageIndex(Number(e.target.value));
                }}
                className="h-9 cursor-pointer appearance-none rounded-full bg-sunken px-4 text-sm text-ink focus-visible:outline-2 focus-visible:outline-focus"
              >
                {session.pages.map((p) => (
                  <option key={p.pageIndex} value={p.pageIndex}>
                    Page {p.pageIndex + 1} of {pageCount}
                    {perPage[p.pageIndex] ? ` · ${perPage[p.pageIndex]} hidden` : ""}
                  </option>
                ))}
              </select>
              <Button
                size="sm"
                aria-label="Next page"
                disabled={pageIndex >= pageCount - 1}
                onClick={() => {
                  onSelect(null);
                  onPageIndex(pageIndex + 1);
                }}
                className="size-9 px-0"
              >
                <ChevronRight aria-hidden className="size-5" strokeWidth={1.5} />
              </Button>
            </div>
            <Button size="sm" aria-pressed={drawMode} onClick={() => setDrawMode((d) => !d)} className={cn(drawMode && "bg-ink text-white shadow-none hover:bg-ink-2")}>
              <SquareDashedMousePointer aria-hidden className="size-4" strokeWidth={1.5} />
              {drawMode ? "Drawing: drag on the page" : "Draw a box"}
            </Button>
          </div>
          {page && (
            <PageCanvas
              renderer={page.renderer}
              label={`Page ${pageIndex + 1} of ${pageCount}`}
              candidates={onPage}
              selectedId={selectedId}
              onSelect={onSelect}
              drawMode={drawMode}
              onDraw={draw}
              onMove={(id, rect) => update(id, rect)}
              lockedIds={lockedIds}
            />
          )}
          {page?.text?.source === "ocr" && (
            <p className="text-sm text-muted">This page is a scan. Text was read on your device, so check the boxes closely.</p>
          )}
          {page?.preRedacted && (
            <p className="text-sm text-muted">This is the image that was already sent. Add boxes for anything still visible.</p>
          )}
        </Surface>

        <Surface as="aside" aria-label="Boxes" className="flex flex-col gap-5 p-5 sm:p-6">
          <div className="flex flex-col gap-1">
            <SectionTitle as="h3" className="[&_h3]:text-xl">
              On this page
            </SectionTitle>
            <p className="text-base text-muted">
              {enabledCount} {enabledCount === 1 ? "box" : "boxes"} on {pagesWithBoxes} of {pageCount} {pageCount === 1 ? "page" : "pages"}
            </p>
          </div>

          {onPage.length === 0 ? (
            <p className="rounded-tile bg-sunken px-4 py-3 text-base text-ink-2">Nothing found on this page. Draw a box if something should be hidden.</p>
          ) : (
            <ul className="-mx-2 flex flex-col gap-1">
              {onPage.map((c) => {
                const selected = c.box.id === selectedId;
                const locked = lockedIds.has(c.box.id);
                const manual = c.box.source !== "detector";
                return (
                  <li
                    key={c.box.id}
                    className={cn(
                      "flex items-center gap-3 rounded-tile px-2 py-2 transition-colors",
                      selected ? "bg-sunken ring-1 ring-focus" : "hover:bg-sunken/60",
                    )}
                  >
                    <Switch
                      checked={c.box.enabled}
                      disabled={locked}
                      label={`Hide ${c.value || c.label}`}
                      onChange={(enabled) => update(c.box.id, { enabled })}
                    />
                    <button
                      type="button"
                      aria-pressed={selected}
                      onClick={() => onSelect(selected ? null : c.box.id)}
                      className="flex min-w-0 flex-1 flex-col items-start rounded-lg text-left"
                    >
                      <span className={cn("w-full truncate text-base", c.box.enabled ? "text-ink" : "text-muted line-through")}>
                        {c.value || c.label}
                      </span>
                      <span className="w-full truncate text-sm text-muted">
                        {c.value ? c.label : locked ? "Already hidden" : "Drawn by hand"}
                        {c.box.source === "detector" && c.box.confidence < 0.95 && ` · ${Math.round(c.box.confidence * 100)}% sure`}
                      </span>
                    </button>
                    <TokenSelect
                      value={c.box.token}
                      used={used}
                      label={`Label for ${c.value || c.label}`}
                      disabled={locked}
                      onChange={(token) => changeToken(c, token)}
                    />
                    {manual && !locked && (
                      <button
                        type="button"
                        aria-label={`Remove ${c.value || "this box"}`}
                        onClick={() => {
                          onCandidates(candidates.filter((x) => x.box.id !== c.box.id));
                          if (selected) onSelect(null);
                        }}
                        className="grid size-9 shrink-0 place-items-center rounded-full text-muted hover:bg-surface hover:text-negative"
                      >
                        <Trash aria-hidden className="size-4" strokeWidth={1.5} />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <form onSubmit={findAll} className="flex flex-col gap-3 border-t border-line pt-5">
            <label htmlFor={`${ids}-find`} className="text-base font-medium text-ink">
              Hide something else
            </label>
            <TextInput
              id={`${ids}-find`}
              value={findValue}
              onChange={(e) => setFindValue(e.target.value)}
              placeholder="A name, number or phrase"
              autoComplete="off"
            />
            <div className="flex items-center gap-2">
              <TokenSelect value={findToken} used={used} label="Label for the value" onChange={setFindToken} className="max-w-none flex-1" />
              <Button type="submit" size="sm" disabled={!findValue.trim()}>
                <Search aria-hidden className="size-4" strokeWidth={1.5} />
                Find all
              </Button>
            </div>
          </form>
          <p role="status" aria-live="polite" className="min-h-6 text-sm text-ink-2">
            {announcement}
          </p>
        </Surface>
      </div>

      <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
        {onBack ? (
          <Button variant="ghost" onClick={onBack}>
            Back
          </Button>
        ) : (
          <span />
        )}
        <Button variant="primary" size="lg" onClick={onContinue}>
          Prepare images
        </Button>
      </div>
    </div>
  );
}
