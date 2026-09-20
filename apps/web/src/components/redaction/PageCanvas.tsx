"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import { browserCanvasFactory } from "@/redaction/canvas";
import type { BoxCandidate, PageRenderer, Rect } from "@/redaction/types";
import { ctx2d } from "@/redaction/types";
import { cn } from "@/lib/cn";

import { TOKEN_FONT } from "./parts";

type Drag =
  | { kind: "draw"; x: number; y: number; pointerId: number }
  | { kind: "move" | "resize"; id: string; x: number; y: number; orig: Rect; pointerId: number };

const MIN_SIZE = 0.006;
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function normalize(a: { x: number; y: number }, b: { x: number; y: number }): Rect {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) };
}

export function PageCanvas({
  renderer,
  label,
  candidates,
  selectedId,
  onSelect,
  drawMode,
  onDraw,
  onMove,
  lockedIds,
}: {
  renderer: PageRenderer;
  label: string;
  candidates: readonly BoxCandidate[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  drawMode: boolean;
  onDraw: (rect: Rect) => void;
  onMove: (id: string, rect: Rect) => void;
  lockedIds?: ReadonlySet<string>;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<Drag | null>(null);
  const [width, setWidth] = useState(0);
  const [rendered, setRendered] = useState<PageRenderer | null>(null);
  const [preview, setPreview] = useState<Rect | null>(null);
  const [override, setOverride] = useState<{ id: string; rect: Rect } | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);

  useEffect(() => {
    const el = frameRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Re-render in 160px steps so resizing the window doesn't re-render on every pixel.
  const renderWidth = width ? Math.ceil(width / 160) * 160 : 0;

  useEffect(() => {
    if (!renderWidth) return;
    let cancelled = false;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const scale = (renderWidth * dpr) / renderer.widthPt;
    const off = browserCanvasFactory.create(Math.round(renderer.widthPt * scale), Math.round(renderer.heightPt * scale));
    const offCtx = ctx2d(off);
    offCtx.fillStyle = "#ffffff";
    offCtx.fillRect(0, 0, off.width, off.height);
    renderer
      .render(off, scale)
      .then(() => {
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        canvas.width = off.width;
        canvas.height = off.height;
        canvas.getContext("2d")?.drawImage(off as HTMLCanvasElement, 0, 0);
        setRendered(renderer);
        setRenderError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setRenderError(err instanceof Error ? err.message : "This page could not be displayed");
      });
    return () => {
      cancelled = true;
    };
  }, [renderer, renderWidth]);

  const heightPx = width ? (width * renderer.heightPt) / renderer.widthPt : 0;

  const point = (e: ReactPointerEvent) => {
    const rect = frameRef.current!.getBoundingClientRect();
    return { x: clamp01((e.clientX - rect.left) / rect.width), y: clamp01((e.clientY - rect.top) / rect.height) };
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    const target = (e.target as HTMLElement).closest<HTMLElement>("[data-box-id]");
    const handle = (e.target as HTMLElement).closest<HTMLElement>("[data-resize]");
    const p = point(e);
    if (drawMode && !handle) {
      dragRef.current = { kind: "draw", ...p, pointerId: e.pointerId };
      e.currentTarget.setPointerCapture(e.pointerId);
      setPreview({ x: p.x, y: p.y, w: 0, h: 0 });
      return;
    }
    if (!target) {
      onSelect(null);
      return;
    }
    const id = target.dataset.boxId!;
    onSelect(id);
    const candidate = candidates.find((c) => c.box.id === id);
    if (!candidate || lockedIds?.has(id)) return;
    const { x, y, w, h } = candidate.box;
    dragRef.current = { kind: handle ? "resize" : "move", id, ...p, orig: { x, y, w, h }, pointerId: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const p = point(e);
    if (drag.kind === "draw") {
      setPreview(normalize(drag, p));
      return;
    }
    const dx = p.x - drag.x;
    const dy = p.y - drag.y;
    const o = drag.orig;
    const rect =
      drag.kind === "move"
        ? { x: clamp01(Math.min(1 - o.w, o.x + dx)), y: clamp01(Math.min(1 - o.h, o.y + dy)), w: o.w, h: o.h }
        : { x: o.x, y: o.y, w: Math.max(MIN_SIZE, Math.min(1 - o.x, o.w + dx)), h: Math.max(MIN_SIZE, Math.min(1 - o.y, o.h + dy)) };
    setOverride({ id: drag.id, rect });
  };

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    dragRef.current = null;
    if (drag.kind === "draw") {
      const rect = normalize(drag, point(e));
      setPreview(null);
      if (rect.w >= MIN_SIZE && rect.h >= MIN_SIZE) onDraw(rect);
      return;
    }
    if (override?.id === drag.id) onMove(drag.id, override.rect);
    setOverride(null);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      onSelect(null);
      return;
    }
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const dir = arrows[e.key];
    const selected = candidates.find((c) => c.box.id === selectedId);
    if (!dir || !selected || lockedIds?.has(selected.box.id)) return;
    e.preventDefault();
    const step = e.altKey ? 0.001 : 0.004;
    const { x, y, w, h } = selected.box;
    const next = e.shiftKey
      ? { x, y, w: Math.max(MIN_SIZE, Math.min(1 - x, w + dir[0] * step)), h: Math.max(MIN_SIZE, Math.min(1 - y, h + dir[1] * step)) }
      : { x: clamp01(Math.min(1 - w, x + dir[0] * step)), y: clamp01(Math.min(1 - h, y + dir[1] * step)), w, h };
    onMove(selected.box.id, next);
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={frameRef}
        tabIndex={0}
        role="group"
        aria-label={`${label}. Select a box in the list, then use arrow keys to move it and Shift with arrow keys to resize it.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          dragRef.current = null;
          setPreview(null);
          setOverride(null);
        }}
        onKeyDown={onKeyDown}
        className={cn(
          "relative w-full overflow-hidden rounded-tile bg-surface shadow-tile select-none",
          drawMode ? "cursor-crosshair touch-none" : "touch-manipulation",
        )}
        style={{ height: heightPx || undefined, aspectRatio: heightPx ? undefined : `${renderer.widthPt} / ${renderer.heightPt}` }}
      >
        <canvas
          ref={canvasRef}
          aria-hidden
          className={cn("absolute inset-0 size-full transition-opacity duration-200", rendered === renderer ? "opacity-100" : "opacity-0")}
        />
        {rendered !== renderer && !renderError && (
          <div className="absolute inset-0 grid place-items-center text-base text-muted">Rendering page…</div>
        )}
        {renderError && <div className="absolute inset-0 grid place-items-center p-6 text-center text-base text-negative">{renderError}</div>}

        {candidates.map(({ box }) => {
          const rect = override?.id === box.id ? override.rect : box;
          const selected = box.id === selectedId;
          const boxW = rect.w * width;
          const boxH = rect.h * heightPx;
          const fontSize = box.token ? Math.max(6, Math.min(boxH * 0.62, boxW / Math.max(4, box.token.length * 0.62))) : 0;
          const locked = lockedIds?.has(box.id);
          return (
            <div
              key={box.id}
              data-box-id={box.id}
              className={cn(
                "absolute grid place-items-center overflow-visible transition-[opacity,box-shadow] duration-150",
                box.enabled && box.token && (selected ? "border border-ink/70 bg-white/30" : "border border-ink bg-white"),
                box.enabled && !box.token && (selected ? "bg-ink/35" : "bg-ink"),
                !box.enabled && "border border-dashed border-ink-2/70 bg-transparent",
                box.source === "server_suggestion" && !selected && "ring-2 ring-caution ring-offset-1",
                selected && "z-10 outline-2 outline-offset-2 outline-focus",
                !drawMode && (locked ? "cursor-pointer" : "cursor-move"),
              )}
              style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}
            >
              {box.enabled && box.token && (
                <span
                  className={cn("pointer-events-none leading-none font-semibold whitespace-nowrap text-ink", selected && "opacity-0")}
                  style={{ fontSize, ...TOKEN_FONT }}
                >
                  {box.token}
                </span>
              )}
              {selected && !locked && !drawMode && (
                <span
                  data-resize
                  aria-hidden
                  className="absolute -right-2 -bottom-2 size-4 cursor-nwse-resize rounded-full border-2 border-white bg-focus shadow-tile"
                />
              )}
            </div>
          );
        })}

        {preview && (
          <div
            aria-hidden
            className="absolute border-2 border-dashed border-focus bg-focus/10"
            style={{ left: `${preview.x * 100}%`, top: `${preview.y * 100}%`, width: `${preview.w * 100}%`, height: `${preview.h * 100}%` }}
          />
        )}
      </div>
    </div>
  );
}
