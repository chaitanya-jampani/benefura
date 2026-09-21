// Raw values live only in memory or the local `aliases` table; persisted boxes carry geometry and tokens, never text.
import type { AliasKind, RedactionBox } from "@/db/dexie";
import type { Region } from "@/domain/types";

/**
 * Page points (1/72 in), top-left origin, y down. Baseline origin plus unit advance direction, so
 * rotated text still maps characters correctly.
 */
export interface TextRun {
  str: string;
  ox: number;
  oy: number;
  /** (1, 0) for normal left-to-right text. */
  dx: number;
  dy: number;
  advance: number;
  /** Both ≥ 0, measured from the baseline. */
  ascent: number;
  descent: number;
  hasEOL: boolean;
}

export type TextSource = "pdf" | "ocr" | "none";

export interface PageText {
  pageIndex: number;
  widthPt: number;
  heightPt: number;
  runs: TextRun[];
  source: TextSource;
}

/** Page fractions (0..1). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type TokenFamily = "MEMBER" | "EMPLOYER" | "POLICY" | "CERT" | "ADDRESS" | "PHONE" | "EMAIL" | "ID";

export interface KnownValue {
  token: string;
  kind: AliasKind;
  value: string;
  /** Also match first and last names on their own. */
  isName?: boolean;
}

export interface DetectionSignals {
  known?: boolean;
  checksum?: boolean;
  label?: boolean;
  region?: boolean;
}

/** `start`/`end` index the page's text stream. */
export interface Detection {
  pageIndex: number;
  start: number;
  end: number;
  /** Memory only. */
  value: string;
  kind: AliasKind;
  /** null for a plain black box. */
  family: TokenFamily | null;
  token?: string;
  detector: string;
  label: string;
  confidence: number;
  signals: DetectionSignals;
  defaultEnabled: boolean;
}

export interface DetectOptions {
  region: Region;
  known: KnownValue[];
}

/** `value` is never persisted. */
export interface BoxCandidate {
  box: RedactionBox;
  value: string;
  label: string;
}

// The subset of the 2D API that both HTMLCanvasElement and @napi-rs/canvas (Vitest) implement.

export interface Ctx2D {
  fillStyle: unknown;
  strokeStyle: unknown;
  lineWidth: number;
  font: string;
  textAlign: string;
  textBaseline: string;
  imageSmoothingEnabled: boolean;
  imageSmoothingQuality?: string;
  save(): void;
  restore(): void;
  translate(x: number, y: number): void;
  scale(x: number, y: number): void;
  fillRect(x: number, y: number, w: number, h: number): void;
  strokeRect(x: number, y: number, w: number, h: number): void;
  fillText(text: string, x: number, y: number): void;
  measureText(text: string): { width: number };
  drawImage(image: never, dx: number, dy: number, dw: number, dh: number): void;
  getImageData(sx: number, sy: number, sw: number, sh: number): { data: Uint8ClampedArray; width: number; height: number };
  putImageData(data: never, dx: number, dy: number): void;
}

export interface CanvasLike {
  width: number;
  height: number;
  getContext(type: "2d"): unknown;
}

export interface DecodedImage {
  image: unknown;
  width: number;
  height: number;
  close?: () => void;
}

export interface CanvasFactory {
  create(width: number, height: number): CanvasLike;
  encodeJpeg(canvas: CanvasLike, quality: number): Promise<Uint8Array>;
  decode(bytes: Uint8Array | Blob): Promise<DecodedImage>;
  /** iOS Safari caps canvas area near 16.7 MP. */
  maxPixels: number;
}

export function ctx2d(canvas: CanvasLike): Ctx2D {
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2D canvas is not available");
  return ctx as Ctx2D;
}

export interface PageRenderer {
  widthPt: number;
  heightPt: number;
  /** `scale` is pixels per point. */
  render(canvas: CanvasLike, scale: number): Promise<void>;
}
