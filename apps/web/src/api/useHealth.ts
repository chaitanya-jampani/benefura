"use client";

// Container Apps scales to zero, so the API is woken as soon as the app loads.
import { useCallback, useSyncExternalStore } from "react";

import type { HealthResponse } from "@/domain/types";

import { getHealth } from "./client";

export type HealthState =
  | { status: "waking" }
  | { status: "ready"; health: HealthResponse }
  | { status: "ai_off"; health: HealthResponse }
  | { status: "budget_exhausted"; health: HealthResponse }
  | { status: "unreachable" };

const REQUEST_TIMEOUT_MS = 15_000;
// Cold starts are slow; keep trying for about a minute.
const RETRY_DELAYS_MS = [2_000, 4_000, 8_000, 16_000, 24_000];

const WAKING: HealthState = { status: "waking" };

let state: HealthState = WAKING;
let inFlight: Promise<void> | null = null;
let started = false;
const listeners = new Set<() => void>();

function setState(next: HealthState) {
  state = next;
  listeners.forEach((l) => l());
}

export function healthStateFrom(health: HealthResponse): HealthState {
  if (!health.aiEnabled) return { status: "ai_off", health };
  if (health.budgetRemainingPct <= 0) return { status: "budget_exhausted", health };
  return { status: "ready", health };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function wake(): Promise<void> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    if (state.status === "unreachable") setState(WAKING);
    for (let attempt = 0; ; attempt++) {
      try {
        const health = await getHealth(AbortSignal.timeout(REQUEST_TIMEOUT_MS));
        setState(healthStateFrom(health));
        return;
      } catch {
        if (attempt >= RETRY_DELAYS_MS.length) {
          setState({ status: "unreachable" });
          return;
        }
        await sleep(RETRY_DELAYS_MS[attempt]);
      }
    }
  })().finally(() => {
    inFlight = null;
  });
  return inFlight;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!started) {
    started = true;
    void wake();
  }
  return () => listeners.delete(listener);
}

export function useHealth(): { state: HealthState; refresh: () => Promise<void> } {
  const current = useSyncExternalStore(
    subscribe,
    () => state,
    () => WAKING,
  );
  const refresh = useCallback(() => wake(), []);
  return { state: current, refresh };
}

export function currentHealth(): HealthState {
  return state;
}
