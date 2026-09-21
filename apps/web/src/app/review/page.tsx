import { Suspense } from "react";

import { ReviewFlow } from "@/components/review/ReviewFlow";

export const metadata = { title: "Review" };

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ReviewFlow />
    </Suspense>
  );
}
