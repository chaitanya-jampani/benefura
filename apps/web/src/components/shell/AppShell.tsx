import type { ReactNode } from "react";

import { HealthChip } from "@/components/shell/HealthChip";
import { PrivacyInspectorButton } from "@/components/privacy-inspector/PrivacyInspectorButton";

import { Dock } from "./Dock";
import { Wordmark } from "./Wordmark";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="no-print mx-auto flex w-full max-w-[72rem] items-center justify-between gap-4 px-4 pt-6 pb-4 sm:px-8 sm:pt-10">
        <Wordmark />
        <div className="flex items-center gap-3">
          <HealthChip />
          <PrivacyInspectorButton />
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[72rem] flex-1 px-4 pb-40 sm:px-8">
        {children}
      </main>
      <Dock />
    </div>
  );
}
