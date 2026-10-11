import type { ReactNode } from "react";
import { AppProvider } from "@/components/AppProvider";
import { Shell } from "@/components/Shell";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AppProvider>
      <Shell>{children}</Shell>
    </AppProvider>
  );
}
