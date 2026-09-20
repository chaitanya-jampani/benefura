"use client";

import { Building, IdCard, Lock, Mail, MapPin, Phone, Plus, ScrollText, Trash, UserRound, Users } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Button, SectionTitle, Surface } from "@/components/ui";
import { cn } from "@/lib/cn";
import { newId } from "@/redaction/apply";
import type { FamilyRelationship, FormTokens, HideFormValues, SingleField } from "@/redaction/known";

import { Progress, TextInput, TokenPill } from "./parts";

const FIELDS: ReadonlyArray<{
  key: SingleField;
  label: string;
  placeholder: string;
  icon: ReactNode;
  type?: string;
  autoComplete?: string;
}> = [
  { key: "selfName", label: "Your name", placeholder: "As it appears on the booklet", icon: <UserRound strokeWidth={1.5} className="size-5" />, autoComplete: "name" },
  { key: "employer", label: "Employer", placeholder: "Company or organization", icon: <Building strokeWidth={1.5} className="size-5" />, autoComplete: "organization" },
  { key: "policyNumber", label: "Policy or group number", placeholder: "e.g. 88213", icon: <ScrollText strokeWidth={1.5} className="size-5" />, autoComplete: "off" },
  { key: "certificateNumber", label: "Certificate or member ID", placeholder: "e.g. NW-4471-0093", icon: <IdCard strokeWidth={1.5} className="size-5" />, autoComplete: "off" },
  { key: "address", label: "Address", placeholder: "Street, city, postal code", icon: <MapPin strokeWidth={1.5} className="size-5" />, autoComplete: "street-address" },
  { key: "phone", label: "Phone", placeholder: "Any format", icon: <Phone strokeWidth={1.5} className="size-5" />, type: "tel", autoComplete: "tel" },
  { key: "email", label: "Email", placeholder: "you@example.com", icon: <Mail strokeWidth={1.5} className="size-5" />, type: "email", autoComplete: "email" },
];

const RELATIONSHIPS: ReadonlyArray<{ value: FamilyRelationship; label: string }> = [
  { value: "spouse", label: "Spouse or partner" },
  { value: "child", label: "Child" },
  { value: "dependent", label: "Other dependant" },
];

function Row({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-start gap-4">
      <span aria-hidden className="mt-7 hidden size-12 shrink-0 place-items-center rounded-tile bg-surface text-ink shadow-tile sm:grid">
        {icon}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function HideStep({
  form,
  tokens,
  onChange,
  reading,
  savedCount,
  onBack,
  onContinue,
  busy,
}: {
  form: HideFormValues;
  tokens: FormTokens;
  onChange: (next: HideFormValues) => void;
  reading: { done: number; total: number; detail?: string } | null;
  savedCount: number;
  onBack: () => void;
  onContinue: () => void;
  busy: boolean;
}) {
  const ids = useId();
  const set = (key: SingleField, value: string) => onChange({ ...form, [key]: value });

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(e) => {
        e.preventDefault();
        onContinue();
      }}
    >
      <Surface className="flex flex-col gap-8">
        <div className="flex flex-col gap-3">
          <SectionTitle as="h2">What should we hide?</SectionTitle>
          <p className="max-w-2xl text-lg text-muted">
            We find these on every page and paint a label over them, so the assistant can still tell people and policies apart
            without seeing who they are. Leave anything blank that isn&apos;t in your document.
          </p>
          <p className="inline-flex items-center gap-2 text-base text-ink-2">
            <Lock aria-hidden className="size-4" strokeWidth={1.5} />
            {savedCount > 0 ? "Filled in from details saved on this device. " : ""}
            What you type here stays in this browser.
          </p>
        </div>

        {reading && reading.done < reading.total && (
          <Progress label={reading.detail ?? "Reading your document"} value={reading.done} max={reading.total} />
        )}

        <div className="grid gap-x-10 gap-y-6 lg:grid-cols-2">
          {FIELDS.map((f) => (
            <Row key={f.key} icon={f.icon}>
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-3">
                  <label htmlFor={`${ids}-${f.key}`} className="text-base font-medium text-ink">
                    {f.label}
                  </label>
                  <TokenPill token={tokens.fields[f.key]} className={cn(!form[f.key].trim() && "opacity-50")} />
                </div>
                <TextInput
                  id={`${ids}-${f.key}`}
                  type={f.type ?? "text"}
                  value={form[f.key]}
                  placeholder={f.placeholder}
                  autoComplete={f.autoComplete}
                  spellCheck={false}
                  onChange={(e) => set(f.key, e.target.value)}
                />
              </div>
            </Row>
          ))}

          <Row icon={<Users strokeWidth={1.5} className="size-5" />}>
            <fieldset className="flex flex-col gap-3">
              <legend className="mb-2 text-base font-medium text-ink">Family members on the plan</legend>
              {form.family.map((m, i) => (
                <div key={m.id} className="flex flex-col gap-2 rounded-tile bg-sunken/60 p-3">
                  <div className="flex items-center justify-between gap-3">
                    <label htmlFor={`${ids}-fam-${m.id}`} className="text-sm text-muted">
                      Family member {i + 1}
                    </label>
                    <TokenPill token={tokens.family[m.id] ?? null} className={cn(!m.name.trim() && "opacity-50")} />
                  </div>
                  <TextInput
                    id={`${ids}-fam-${m.id}`}
                    value={m.name}
                    placeholder="Full name"
                    autoComplete="off"
                    spellCheck={false}
                    className="bg-surface"
                    onChange={(e) =>
                      onChange({ ...form, family: form.family.map((x) => (x.id === m.id ? { ...x, name: e.target.value } : x)) })
                    }
                  />
                  <div className="flex items-center gap-2">
                    <label htmlFor={`${ids}-rel-${m.id}`} className="sr-only">
                      Relationship for family member {i + 1}
                    </label>
                    <select
                      id={`${ids}-rel-${m.id}`}
                      value={m.relationship}
                      onChange={(e) =>
                        onChange({
                          ...form,
                          family: form.family.map((x) => (x.id === m.id ? { ...x, relationship: e.target.value as FamilyRelationship } : x)),
                        })
                      }
                      className="h-10 flex-1 cursor-pointer rounded-full bg-surface px-4 text-sm text-ink ring-1 ring-line focus-visible:outline-2 focus-visible:outline-focus"
                    >
                      {RELATIONSHIPS.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Remove family member ${i + 1}`}
                      onClick={() => onChange({ ...form, family: form.family.filter((x) => x.id !== m.id) })}
                      className="size-10 px-0"
                    >
                      <Trash aria-hidden className="size-4" strokeWidth={1.5} />
                    </Button>
                  </div>
                </div>
              ))}
              <Button
                size="sm"
                className="self-start"
                onClick={() => onChange({ ...form, family: [...form.family, { id: newId(), name: "", relationship: "spouse" }] })}
              >
                <Plus aria-hidden className="size-4" strokeWidth={1.5} />
                Add a family member
              </Button>
            </fieldset>
          </Row>
        </div>
      </Surface>

      <div className="flex flex-col-reverse items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Button variant="ghost" onClick={onBack}>
          Back
        </Button>
        <Button type="submit" variant="primary" size="lg" disabled={busy}>
          {busy ? "Finding personal details…" : "Find personal details"}
        </Button>
      </div>
    </form>
  );
}
