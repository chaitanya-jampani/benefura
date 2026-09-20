import { Suspense } from "react";

import { ClaimsScreen } from "@/components/claims/ClaimsScreen";
import { DashboardSkeleton } from "@/components/dashboard/DashboardStates";

export const metadata = { title: "Claims" };

export default function Page() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <ClaimsScreen />
    </Suspense>
  );
}
