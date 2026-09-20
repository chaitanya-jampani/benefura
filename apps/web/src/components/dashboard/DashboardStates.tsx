import { DemoActions } from "@/components/demo/DemoActions";
import { Surface } from "@/components/ui";

export function DashboardSkeleton() {
  return (
    <div aria-busy="true" aria-live="polite" className="space-y-6">
      <span className="sr-only">Loading your plan</span>
      <div className="h-16 w-2/3 max-w-md rounded-tile bg-surface/60" />
      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Surface glow className="h-[26rem]">
            <div className="h-6 w-56 rounded-full bg-sunken" />
            <div className="mt-10 h-20 w-64 rounded-tile bg-sunken" />
          </Surface>
          <Surface className="h-96" />
        </div>
        <div className="space-y-6">
          <Surface className="h-56 sm:p-8" />
          <Surface className="h-72 sm:p-8" />
        </div>
      </div>
    </div>
  );
}

export function EmptyPlan({ message }: { message?: string }) {
  return (
    <Surface glow aria-labelledby="empty-title" className="max-w-3xl">
      <h1 id="empty-title" className="text-3xl font-medium tracking-tight text-ink sm:text-4xl">
        No plan in this browser yet
      </h1>
      <p className="mt-4 max-w-xl text-lg text-ink-2">
        {message ??
          "Read your own booklet to see what you can still claim, or open a demo plan built from fictional data."}
      </p>
      <DemoActions className="mt-8" />
    </Surface>
  );
}
