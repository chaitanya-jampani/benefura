// The original file is never written; boxes carry geometry and tokens only.
import { db, type AliasRecord, type DocumentRecord, type PageRecord } from "@/db/dexie";

import { AliasBook, FAMILY_KIND, parseToken } from "./apply";
import { jpegBlob } from "./canvas";
import { planChunks } from "./chunk-pdf";
import { applyFormToBook, type HideFormValues } from "./known";
import type { RedactionSession } from "./session";

/** Includes every enabled labelled box so later receipts and chat messages get the same tokens. */
export function aliasesFromSession(session: RedactionSession, form: HideFormValues | null, saved: readonly AliasRecord[]): AliasBook {
  const book = new AliasBook(saved);
  if (form) applyFormToBook(form, book);
  for (const { box, value } of session.candidates) {
    if (!box.enabled || !box.token || !value.trim()) continue;
    const family = parseToken(box.token)?.family;
    book.assign(box.token, family ? FAMILY_KIND[family] : box.kind, value);
  }
  return book;
}

export async function saveRedactedDocument(input: {
  session: RedactionSession;
  form: HideFormValues | null;
  acknowledgedAt: string;
}): Promise<DocumentRecord> {
  const { session, acknowledgedAt } = input;
  const now = acknowledgedAt;
  const existing = await db.documents.get(session.docId);
  const saved = await db.aliases.toArray();
  const book = aliasesFromSession(session, input.form, saved);
  const { put, remove } = book.changed();

  const doc: DocumentRecord = {
    id: session.docId,
    region: session.region,
    name: session.name,
    pageCount: session.pages.length,
    status: "acknowledged",
    isSample: session.isSample,
    acknowledgedAt,
    planId: existing?.planId,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
  };

  const pages: PageRecord[] = session.finalPages.map((raster) => {
    const page = session.pages.find((p) => p.pageIndex === raster.pageIndex)!;
    return {
      id: `${session.docId}:${raster.pageIndex}`,
      documentId: session.docId,
      pageIndex: raster.pageIndex,
      widthPx: raster.widthPx,
      heightPx: raster.heightPx,
      dpi: raster.dpi,
      image: jpegBlob(raster.jpeg),
      boxes: session.candidates.filter((c) => c.box.pageIndex === raster.pageIndex).map((c) => ({ ...c.box })),
      textSource: page.preRedacted ? "none" : (page.text?.source ?? "none"),
    };
  });

  const changedPages = new Set([...session.changedSinceSave].map((i) => i + 1));
  const staleChunks = existing
    ? planChunks(session.pages.length)
        .map((chunk, i) => ({ id: `${session.docId}:${i}`, pages: chunk.map((p) => p + 1) }))
        .filter((c) => c.pages.some((p) => changedPages.has(p)))
        .map((c) => c.id)
    : [];

  await db.transaction("rw", [db.documents, db.pages, db.aliases, db.extractions], async () => {
    await db.documents.put(doc);
    await db.pages.bulkPut(pages);
    if (put.length) await db.aliases.bulkPut(put);
    if (remove.length) await db.aliases.bulkDelete(remove);
    if (staleChunks.length) await db.extractions.bulkDelete(staleChunks);
  });
  session.changedSinceSave.clear();
  return doc;
}
