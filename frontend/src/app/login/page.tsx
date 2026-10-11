"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();

  // Already signed in? Go straight to the dashboard.
  useEffect(() => {
    api("/me")
      .then(() => router.replace("/"))
      .catch(() => {});
  }, [router]);

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-5 py-10">
      <div className="rounded-2xl border border-line bg-surface p-6 text-center shadow-sm sm:p-8">
        <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary text-primary-fg">
          <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </div>
        <h1 className="text-2xl font-bold">Smart To-Do</h1>
        <p className="mt-2 text-muted">
          Your inbox, turned into tasks with deadlines and a one-tap link to finish each one.
        </p>

        {/* A full page navigation on purpose: the backend redirects on to Google. */}
        <a
          href="/api/auth/login"
          className="mt-6 flex w-full items-center justify-center gap-3 rounded-xl border border-line bg-surface px-5 py-3.5 text-base font-bold text-fg shadow-sm hover:bg-surface-2"
        >
          <svg viewBox="0 0 48 48" width="22" height="22" aria-hidden="true">
            <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.6 17.7 9.5 24 9.5z" />
            <path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.3 5.5-4.8 7.2l7.5 5.8c4.4-4.1 7.1-10.1 7.1-17.5z" />
            <path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 0 1 0-9.4l-7.9-6.1a24 24 0 0 0 0 21.6l7.9-6.1z" />
            <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.5-5.8c-2.1 1.4-4.8 2.3-8.4 2.3-6.3 0-11.6-4.1-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z" />
          </svg>
          Sign in with Google
        </a>

        <p className="mt-5 text-xs text-muted">
          You will be asked to let this app read your Gmail (to find tasks) and add events to your
          Google Calendar. Email bodies are never stored.
        </p>
      </div>
    </main>
  );
}
