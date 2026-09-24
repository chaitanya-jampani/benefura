import { Archive, Globe, MonitorSmartphone, ScanEye, Send, ShieldCheck, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { OutboundLog } from "@/components/privacy-inspector/OutboundLog";
import { IconTile, SectionTitle, Surface } from "@/components/ui";

export const metadata = { title: "Privacy" };

function Point({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="flex flex-col gap-1 py-4">
      <span className="text-lg font-medium text-ink">{title}</span>
      <span className="text-base leading-relaxed text-ink-2">{children}</span>
    </li>
  );
}

function Section({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <Surface className="flex flex-col gap-2">
      <div className="flex items-center gap-4">
        <IconTile className="size-12 sm:size-12">
          <Icon aria-hidden className="size-5" strokeWidth={1.5} />
        </IconTile>
        <SectionTitle>{title}</SectionTitle>
      </div>
      <ul className="divide-y divide-line">{children}</ul>
    </Surface>
  );
}

export default function Page() {
  return (
    <div className="flex flex-col gap-6">
      <Surface glow className="flex flex-col gap-4">
        <h1 className="text-[clamp(2.25rem,6vw,3.5rem)] leading-tight font-light tracking-tight text-ink">
          Your data stays yours
        </h1>
        <p className="max-w-2xl text-lg leading-relaxed text-ink-2">
          Benefura redacts your documents in this browser and keeps your plan, claims and chats on this device. Only
          what you approve is sent to the Benefura API, and every request is listed at the bottom of this page.
        </p>
      </Surface>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section icon={Send} title="What leaves your browser">
          <Point title="Redacted page images">
            Booklet pages with the boxes and alias labels burned in, and no text layer. You see and approve the exact
            images first.
          </Point>
          <Point title="Redacted receipt images">Receipts go through the same redaction and approval before upload.</Point>
          <Point title="Chat messages, after aliasing">
            Names and numbers you saved are replaced with aliases such as [MEMBER_A] before a message is sent.
          </Point>
          <Point title="Tool results">
            When the assistant looks something up in your plan or claims, it runs here and only a short, aliased result
            is sent back.
          </Point>
          <Point title="A health check">A small request when the app opens, to wake the API.</Point>
        </Section>

        <Section icon={MonitorSmartphone} title="What never leaves this device">
          <Point title="Your original files">The booklet and receipts you pick are read here and never uploaded.</Point>
          <Point title="Your alias map">
            The real names and numbers behind each alias stay in this browser&apos;s storage.
          </Point>
          <Point title="Your records in bulk">
            Your plan, claims and chat history live in IndexedDB on this device. Delete them any time in Settings.
          </Point>
        </Section>

        <Section icon={Archive} title="What Azure keeps">
          <Point title="Document analysis results, up to 24 hours">
            Azure AI Content Understanding stores results briefly; the API deletes them as soon as it has read them.
          </Point>
          <Point title="Abuse monitoring, up to 30 days">
            Microsoft may keep prompts and outputs to detect misuse of the AI models.
          </Point>
          <Point title="Traces, 30 days">
            Application Insights keeps request traces. Traces of the Foundry prompt agents include your aliased chat
            messages, and that can&apos;t be switched off. Content recording in Benefura&apos;s own tracing is off.
          </Point>
        </Section>

        <Section icon={Globe} title="Where it's processed">
          <Point title="East US 2">
            The API and the Microsoft Foundry models run in East US 2, with Global Standard model deployments.
          </Point>
          <Point title="Canada Central, public content only">
            The search index of public reference pages may sit in Canada Central. It never holds anything of yours.
          </Point>
        </Section>
      </div>

      <Section icon={ShieldCheck} title="Checks on the server are a backstop">
        <Point title="Redacting in your browser is the real control">
          The API checks what it receives for identifiers such as social insurance, health card, tax file or Medicare
          numbers and refuses them. For images, that check happens after the image has already been analysed, so
          redaction before sending is what protects you.
        </Point>
        <Point title="Chat messages are checked before any model sees them">
          If a message looks like it contains one of those identifiers, it isn&apos;t sent to the assistant and you&apos;re
          asked to remove it.
        </Point>
      </Section>

      <Surface className="flex flex-col gap-4" aria-labelledby="outbound-log-title">
        <div className="flex items-center gap-4">
          <IconTile className="size-12 sm:size-12">
            <ScanEye aria-hidden className="size-5" strokeWidth={1.5} />
          </IconTile>
          <h2 id="outbound-log-title" className="text-2xl font-medium tracking-tight text-ink sm:text-[1.75rem]">
            Everything sent from this browser
          </h2>
        </div>
        <OutboundLog />
      </Surface>
    </div>
  );
}
