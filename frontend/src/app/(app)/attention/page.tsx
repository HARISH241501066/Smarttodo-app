"use client";

import { ReviewSection } from "@/components/ReviewSection";
import { SuspiciousSection } from "@/components/SuspiciousSection";

/** The shield icon opens this: emails held as suspicious and emails that need a human look. */
export default function AttentionPage() {
  return (
    <div className="space-y-8">
      <h1 className="text-xl font-bold">Needs your attention</h1>
      <SuspiciousSection />
      <ReviewSection />
    </div>
  );
}
