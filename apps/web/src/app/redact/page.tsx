import { Suspense } from "react";

import { RedactFlow } from "@/components/redaction/RedactFlow";

export const metadata = { title: "Redact" };

export default function Page() {
  return (
    <Suspense fallback={null}>
      <RedactFlow />
    </Suspense>
  );
}
