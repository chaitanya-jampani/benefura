import { EyeOff, HardDrive, ImageUp } from "lucide-react";

import { ContinuePlan } from "@/components/demo/ContinuePlan";
import { DemoActions } from "@/components/demo/DemoActions";
import { IconTile, Surface } from "@/components/ui";

const PROMISES = [
  {
    icon: EyeOff,
    title: "Redaction happens in your browser",
    body: "Names, member numbers and addresses are covered on your device before anything is sent.",
  },
  {
    icon: ImageUp,
    title: "Only images you approve leave",
    body: "You see the exact redacted page images first. No original file or text layer is uploaded.",
  },
  {
    icon: HardDrive,
    title: "Your plan and claims stay here",
    body: "They live in this browser's storage. Delete them or export them from Settings at any time.",
  },
];

export default function Home() {
  return (
    <div className="space-y-6">
      <Surface glow aria-labelledby="home-title" className="overflow-hidden">
        <h1 id="home-title" className="max-w-3xl text-4xl leading-[1.08] font-medium tracking-tight text-ink sm:text-6xl">
          See what your health plan still owes you.
        </h1>
        <p className="mt-6 max-w-2xl text-lg text-ink-2 sm:text-xl">
          Benefura reads your extended health booklet or private health policy, then shows what&apos;s left to claim for each
          person, when limits reset and what a new claim should pay back.
        </p>
        <DemoActions className="mt-10" />
        <p className="mt-6 text-base text-muted">The demos use fictional plans and people, and work without an account.</p>
      </Surface>

      <ContinuePlan />

      <section aria-labelledby="privacy-title" className="px-1 pt-4">
        <h2 id="privacy-title" className="text-2xl font-medium tracking-tight text-ink sm:text-[1.75rem]">
          Private by design
        </h2>
        <ul className="mt-4 grid gap-4 md:grid-cols-3">
          {PROMISES.map(({ icon: Icon, title, body }) => (
            <li key={title} className="flex gap-4 rounded-tile bg-surface/60 p-5 md:flex-col">
              <IconTile>
                <Icon aria-hidden strokeWidth={1.5} className="size-6" />
              </IconTile>
              <div>
                <p className="text-lg font-medium text-ink">{title}</p>
                <p className="mt-1 text-base text-ink-2">{body}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
