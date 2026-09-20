"use client";

import { Download, RotateCcw, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { DemoActions } from "@/components/demo/DemoActions";
import { Button, Surface } from "@/components/ui";
import { Field, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Switch } from "@/components/ui/Switch";
import { setSetting, SETTING_KEYS } from "@/db/dexie";
import { usePlans, useSetting } from "@/db/hooks";
import { deleteAllData, downloadJson, exportData, exportFileName, resetDemo } from "@/db/maintenance";

const DEFAULT_API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function Section({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  return (
    <Surface aria-labelledby={id} className="sm:p-8">
      <h2 id={id} className="text-xl font-medium tracking-tight text-ink">
        {title}
      </h2>
      {description && <p className="mt-1 max-w-2xl text-base text-muted">{description}</p>}
      <div className="mt-6">{children}</div>
    </Surface>
  );
}

export function SettingsScreen() {
  const apiOverride = useSetting<string | null>(SETTING_KEYS.apiBaseUrl, null);
  const dpi = useSetting<number>(SETTING_KEYS.rasterDpi, 300);
  const includeSubmitted = useSetting<boolean>(SETTING_KEYS.includeSubmittedClaims, false);
  const plans = usePlans();
  const demos = plans?.filter((p) => p.isDemo) ?? [];
  const [resetState, setResetState] = useState<"idle" | "busy" | "done">("idle");

  return (
    <div className="space-y-6">
      <header className="px-1">
        <h1 className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">Settings</h1>
        <p className="mt-1 text-base text-muted">Everything here is stored in this browser only.</p>
      </header>

      <Section id="api-title" title="Assistant connection" description="Where the browser sends redacted images and aliased chat messages.">
        {apiOverride !== undefined && <ApiUrlForm key={apiOverride ?? "default"} current={apiOverride} />}
      </Section>

      <Section
        id="redaction-title"
        title="Redaction image quality"
        description="Redacted pages are sent as images. Higher resolution reads small print better; lower resolution uploads faster."
      >
        {dpi !== undefined && (
          <SegmentedControl
            label="Image resolution"
            value={String(dpi) as "200" | "300"}
            onChange={(v) => setSetting(SETTING_KEYS.rasterDpi, Number(v))}
            segments={[
              { value: "200", label: "200 DPI" },
              { value: "300", label: "300 DPI" },
            ]}
          />
        )}
      </Section>

      <Section id="claims-title" title="Claims">
        {includeSubmitted !== undefined && (
          <Switch
            checked={includeSubmitted}
            onChange={(v) => setSetting(SETTING_KEYS.includeSubmittedClaims, v)}
            className="max-w-2xl"
            label="Count submitted claims by default"
            description="Limits and remaining amounts treat claims awaiting a decision as paid at their estimate."
          />
        )}
      </Section>

      <Section id="data-title" title="Your data" description="Plans, claims, receipts and aliases live in this browser's storage.">
        <div className="space-y-8">
          <div>
            <h3 className="text-base font-medium text-ink">Demo plans</h3>
            {demos.length > 0 || resetState !== "idle" ? (
              <ResetDemo count={demos.length} state={resetState} setState={setResetState} />
            ) : (
              <>
                <p className="mt-1 text-base text-ink-2">No demo plan is loaded.</p>
                <DemoActions className="mt-4" showBooklet={false} />
              </>
            )}
          </div>
          <ExportData />
          <DeleteAll />
        </div>
      </Section>
    </div>
  );
}

function ApiUrlForm({ current }: { current: string | null }) {
  const [value, setValue] = useState(current ?? "");
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const save = async () => {
    const trimmed = value.trim();
    if (!trimmed) {
      await setSetting(SETTING_KEYS.apiBaseUrl, null);
      setMessage({ tone: "ok", text: "Using the default address." });
      return;
    }
    try {
      const url = new URL(trimmed);
      if (url.protocol !== "https:" && url.protocol !== "http:") throw new Error();
      await setSetting(SETTING_KEYS.apiBaseUrl, url.origin + url.pathname.replace(/\/+$/, ""));
      setMessage({ tone: "ok", text: "Saved. New requests use this address." });
    } catch {
      setMessage({ tone: "error", text: "Enter a full address starting with https://" });
    }
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      className="space-y-4"
    >
      <Field label="API address" htmlFor="api-url" hint={`Leave empty to use the default, ${DEFAULT_API}.`}>
        <TextInput id="api-url" type="url" inputMode="url" placeholder={DEFAULT_API} value={value} onChange={(e) => setValue(e.target.value)} className="max-w-xl" />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" size="sm">
          Save address
        </Button>
        {current && (
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              setValue("");
              await setSetting(SETTING_KEYS.apiBaseUrl, null);
              setMessage({ tone: "ok", text: "Using the default address." });
            }}
          >
            Use the default
          </Button>
        )}
        {message && (
          <p role={message.tone === "error" ? "alert" : "status"} className={message.tone === "error" ? "text-base text-negative" : "text-base text-ink-2"}>
            {message.text}
          </p>
        )}
      </div>
    </form>
  );
}

// State lives in the parent: reloading briefly removes the demo plan, which would unmount this mid-reset.
function ResetDemo({
  count,
  state,
  setState,
}: {
  count: number;
  state: "idle" | "busy" | "done";
  setState: (state: "idle" | "busy" | "done") => void;
}) {
  return (
    <div className="mt-1">
      <p className="text-base text-ink-2">
        Reloads {count > 1 ? "both demo plans" : "the demo plan"} with fresh claims dated from today. Your own plans aren&apos;t touched.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button
          variant="soft"
          size="sm"
          disabled={state === "busy"}
          onClick={async () => {
            setState("busy");
            await resetDemo();
            setState("done");
          }}
        >
          <RotateCcw aria-hidden strokeWidth={1.5} className="size-4" />
          {state === "busy" ? "Resetting" : "Reset demo"}
        </Button>
        {state === "done" && (
          <p role="status" className="text-base text-ink-2">
            Demo reset.
          </p>
        )}
      </div>
    </div>
  );
}

function ExportData() {
  const [includeAliases, setIncludeAliases] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <div>
      <h3 className="text-base font-medium text-ink">Export</h3>
      <p className="mt-1 max-w-2xl text-base text-ink-2">
        Downloads a JSON file with your plans, claims and receipt details. Redacted images are left out.
      </p>
      <label className="mt-4 flex max-w-2xl cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={includeAliases}
          onChange={(e) => setIncludeAliases(e.target.checked)}
          className="mt-1 size-5 shrink-0 accent-ink"
        />
        <span className="text-base text-ink-2">
          Include aliases
          <span className="block text-sm text-muted">
            Adds the real names and numbers behind tokens like [MEMBER_A]. Only tick this for a file you&apos;ll keep private.
          </span>
        </span>
      </label>
      <Button
        variant="soft"
        size="sm"
        className="mt-4"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            downloadJson(await exportData({ includeAliases }), exportFileName());
          } finally {
            setBusy(false);
          }
        }}
      >
        <Download aria-hidden strokeWidth={1.5} className="size-4" />
        Export JSON
      </Button>
    </div>
  );
}

function DeleteAll() {
  const [step, setStep] = useState<"idle" | "confirm" | "done">("idle");
  return (
    <div>
      <h3 className="text-base font-medium text-ink">Delete everything</h3>
      <p className="mt-1 max-w-2xl text-base text-ink-2">
        Removes every plan, claim, receipt, alias, redacted page, chat and setting from this browser. This can&apos;t be undone.
      </p>
      {step === "idle" && (
        <Button variant="danger" size="sm" className="mt-4" onClick={() => setStep("confirm")}>
          <Trash2 aria-hidden strokeWidth={1.5} className="size-4" />
          Delete all data
        </Button>
      )}
      {step === "confirm" && (
        <div role="alertdialog" aria-labelledby="delete-confirm" className="mt-4 rounded-tile bg-sunken p-5">
          <p id="delete-confirm" className="text-base text-ink">
            Delete all Benefura data in this browser?
          </p>
          <div className="mt-3 flex flex-wrap gap-3">
            <Button
              variant="danger"
              size="sm"
              autoFocus
              onClick={async () => {
                await deleteAllData();
                setStep("done");
              }}
            >
              Yes, delete everything
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setStep("idle")}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {step === "done" && (
        <p role="status" className="mt-4 text-base text-ink-2">
          All data deleted.
        </p>
      )}
    </div>
  );
}
