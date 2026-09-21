"use client";

import { Clock, Cloud, Eye, ServerCog, ShieldCheck } from "lucide-react";
import Image from "next/image";
import { useId, type ReactNode } from "react";

import { Button, SectionTitle, Surface } from "@/components/ui";
import { cn } from "@/lib/cn";

import { Notice, TOKEN_FONT } from "./parts";
import type { PreviewImage } from "./PreviewStep";

/** Mirrors docs/privacy.md; keep the two in sync. */
export function AzureDisclosure({ subject }: { subject: string }) {
  const items: Array<{ icon: ReactNode; text: ReactNode }> = [
    {
      icon: <Eye className="size-5" strokeWidth={1.5} />,
      text: (
        <>
          Only {subject} leave this device. Your original file, the details you typed, and your label list stay in this browser.
        </>
      ),
    },
    {
      icon: <ServerCog className="size-5" strokeWidth={1.5} />,
      text: (
        <>
          Azure Content Understanding may keep each analysis result for up to 24 hours. Benefura deletes it as soon as it has read
          it.
        </>
      ),
    },
    {
      icon: <ShieldCheck className="size-5" strokeWidth={1.5} />,
      text: <>Azure OpenAI abuse monitoring may keep what is sent for up to 30 days.</>,
    },
    {
      icon: <Clock className="size-5" strokeWidth={1.5} />,
      text: (
        <>
          Application Insights keeps request traces for 30 days. Agent traces can include labelled text such as{" "}
          <span style={TOKEN_FONT}>[MEMBER_A]</span>, never the values behind the labels.
        </>
      ),
    },
    {
      icon: <Cloud className="size-5" strokeWidth={1.5} />,
      text: <>Processing happens in Azure East US 2 and Azure OpenAI global deployments.</>,
    },
  ];
  return (
    <ul className="flex flex-col gap-4">
      {items.map((item, i) => (
        <li key={i} className="flex items-start gap-4">
          <span aria-hidden className="grid size-11 shrink-0 place-items-center rounded-tile bg-surface text-ink shadow-tile">
            {item.icon}
          </span>
          <p className="pt-2 text-base text-ink-2">{item.text}</p>
        </li>
      ))}
    </ul>
  );
}

export const BACKSTOP_TEXT =
  "The server double-checks for identifiers you missed and stops if it finds one, but by then the image has already been processed by Content Understanding and abuse monitoring. Your redaction here is the real protection.";

export function AcknowledgeCheckbox({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <div
      className={cn(
        "flex items-start gap-4 rounded-tile px-4 py-4 transition-colors",
        checked ? "bg-sunken ring-1 ring-ink/20" : "bg-sunken/60 ring-1 ring-line",
      )}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 size-6 shrink-0 cursor-pointer accent-ink"
      />
      <label htmlFor={id} className="cursor-pointer text-base text-ink">
        {children}
      </label>
    </div>
  );
}

export function AcknowledgeStep({
  images,
  checked,
  onChecked,
  saving,
  error,
  onBack,
  onContinue,
  resend,
}: {
  images: readonly PreviewImage[];
  checked: boolean;
  onChecked: (checked: boolean) => void;
  saving: boolean;
  error: string | null;
  onBack: () => void;
  onContinue: () => void;
  resend: boolean;
}) {
  const shown = images.slice(0, 6);
  const more = images.length - shown.length;
  return (
    <div className="flex flex-col gap-6">
      <Surface className="flex flex-col gap-8">
        <div className="flex flex-col gap-3">
          <SectionTitle as="h2">Before you send</SectionTitle>
          <p className="max-w-2xl text-lg text-muted">
            {resend
              ? "The updated images replace the ones that were stopped. Here is what happens to them once they leave this device."
              : "Here is what happens to your page images once they leave this device."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2" aria-label={`${images.length} page images`}>
          {shown.map((img) => (
            <Image
              key={img.pageIndex}
              src={img.url}
              alt=""
              width={img.widthPx}
              height={img.heightPx}
              unoptimized
              className="h-20 w-auto rounded-md bg-surface shadow-tile ring-1 ring-line sm:h-24"
            />
          ))}
          {more > 0 && <span className="grid h-20 w-14 place-items-center rounded-md bg-sunken text-sm text-muted sm:h-24">+{more}</span>}
        </div>

        <AzureDisclosure subject={`these ${images.length} page ${images.length === 1 ? "image" : "images"}`} />
        <Notice tone="muted">{BACKSTOP_TEXT}</Notice>

        <AcknowledgeCheckbox checked={checked} onChange={onChecked}>
          I&apos;ve checked every page image and I&apos;m OK sending them for analysis.
        </AcknowledgeCheckbox>
        {error && <Notice tone="negative">{error}</Notice>}
      </Surface>

      <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Button variant="ghost" onClick={onBack} disabled={saving}>
          Back to images
        </Button>
        <Button variant="primary" size="lg" disabled={!checked || saving} onClick={onContinue}>
          {saving ? "Saving…" : "Continue"}
        </Button>
      </div>
    </div>
  );
}
