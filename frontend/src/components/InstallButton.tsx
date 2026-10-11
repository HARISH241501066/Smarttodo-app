"use client";

import { useEffect, useState } from "react";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** Shows an "Install" button once the browser says the app is installable (Chrome/Edge/Android). */
export function InstallButton() {
  const [event, setEvent] = useState<InstallPromptEvent | null>(null);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault(); // keep it for our own button
      setEvent(e as InstallPromptEvent);
    };
    const onInstalled = () => setEvent(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!event) return null;
  return (
    <button
      type="button"
      onClick={async () => {
        await event.prompt();
        await event.userChoice;
        setEvent(null);
      }}
      className="mr-1 rounded-lg border border-primary px-3 py-1.5 text-sm font-bold text-primary hover:bg-primary/10"
    >
      Install app
    </button>
  );
}
