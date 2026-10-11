"use client";

import { useEffect } from "react";
import { registerServiceWorker } from "@/lib/push";

/** Registers /sw.js on every page: it makes the app installable, receives push, and shows the
 *  offline page. */
export function RegisterServiceWorker() {
  useEffect(() => {
    void registerServiceWorker();
  }, []);
  return null;
}
