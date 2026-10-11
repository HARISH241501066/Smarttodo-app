"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useState, type ComponentType, type ReactNode, type SVGProps } from "react";
import { useApp } from "@/components/AppProvider";
import { CalendarIcon, FolderIcon, GearIcon, InboxIcon, SearchIcon, ShieldIcon, UserIcon, XIcon } from "@/components/icons";
import { FolderNav } from "@/components/FolderNav";
import { NotificationBell } from "@/components/NotificationBell";
import { ThemeToggle } from "@/components/ThemeToggle";
import { InstallButton } from "@/components/InstallButton";

interface Tab {
  href: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}

/** Header with the bell and theme toggle; navigation is a bottom bar on phones, inline from `sm`. */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { me, reviewCount, suspiciousCount } = useApp();
  const attention = reviewCount + suspiciousCount;
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    if (!drawer) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer]);

  const tabs: Tab[] = [
    { href: "/", label: "My Tasks", Icon: UserIcon },
    { href: "/email", label: "From Email", Icon: InboxIcon },
    { href: "/deadlines", label: "Deadlines", Icon: CalendarIcon },
  ];

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-4xl items-center gap-2 px-3 sm:px-4">
          <Link href="/" className="mr-1 flex items-center gap-2 font-bold" aria-label="Smart To-Do home">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </span>
            <span className="hidden min-[420px]:inline">Smart To-Do</span>
          </Link>

          <nav aria-label="Main" className="ml-2 hidden items-center gap-1 sm:flex">
            {tabs.map((tab) => (
              <TabLink key={tab.href} tab={tab} active={isActive(tab.href)} />
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => setDrawer(true)}
              aria-label="Open folders"
              aria-expanded={drawer}
              data-testid="folders-button"
              className="rounded-lg p-2 text-muted hover:bg-surface-2 md:hidden"
            >
              <FolderIcon width={20} height={20} />
            </button>
            <Link
              href="/search"
              aria-label="Search"
              title="Search"
              className={`rounded-lg p-2 hover:bg-surface-2 ${pathname.startsWith("/search") ? "text-primary" : "text-muted"}`}
            >
              <SearchIcon width={20} height={20} />
            </Link>
            <InstallButton />
            <ThemeToggle />
            {attention > 0 ? (
              <Link
                href="/attention"
                data-testid="attention-link"
                aria-label={`Suspicious and needs review: ${attention} waiting`}
                title="Suspicious and needs review"
                className={`relative rounded-lg p-2 hover:bg-surface-2 ${pathname.startsWith("/attention") ? "text-primary" : "text-muted"}`}
              >
                <ShieldIcon width={20} height={20} />
                <span
                  data-testid="count-attention"
                  className="absolute -right-0.5 -top-0.5 flex min-h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold leading-none text-white"
                >
                  {attention > 99 ? "99+" : attention}
                </span>
              </Link>
            ) : null}
            <NotificationBell />
            <Link
              href="/settings"
              aria-label="Settings"
              title="Settings"
              className={`rounded-lg p-2 hover:bg-surface-2 ${pathname.startsWith("/settings") ? "text-primary" : "text-muted"}`}
            >
              <GearIcon width={20} height={20} />
            </Link>
          </div>
        </div>
        {me.needs_reauth ? (
          <div className="border-t border-warn/40 bg-warn/10 px-4 py-2 text-center text-sm text-warn">
            Google access expired, so new email is not being scanned.{" "}
            <a className="font-bold underline" href="/api/auth/login">
              Sign in again
            </a>
          </div>
        ) : null}
      </header>

      <div className="mx-auto flex w-full max-w-6xl flex-1 gap-6 px-3 sm:px-4">
        <aside aria-label="Folders sidebar" className="sticky top-16 hidden h-[calc(100dvh-5rem)] w-60 shrink-0 overflow-y-auto pt-4 md:block">
          <Suspense fallback={null}>
            <FolderNav />
          </Suspense>
        </aside>
        <main className="min-w-0 max-w-4xl flex-1 pb-28 pt-4 sm:pb-10">{children}</main>
      </div>

      {drawer ? (
        <div className="fixed inset-0 z-40 md:hidden" data-testid="folder-drawer">
          <button
            type="button"
            aria-label="Close folders"
            className="absolute inset-0 bg-black/40"
            onClick={() => setDrawer(false)}
          />
          <div
            role="dialog"
            aria-label="Folders"
            className="absolute inset-y-0 left-0 w-72 max-w-[85vw] overflow-y-auto bg-surface p-3 shadow-xl"
          >
            <div className="mb-2 flex justify-end">
              <button
                type="button"
                aria-label="Close drawer"
                onClick={() => setDrawer(false)}
                className="rounded-lg p-2 text-muted hover:bg-surface-2"
              >
                <XIcon width={18} height={18} />
              </button>
            </div>
            <Suspense fallback={null}>
              <FolderNav onNavigate={() => setDrawer(false)} />
            </Suspense>
          </div>
        </div>
      ) : null}

      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden"
      >
        {tabs.map((tab) => (
          <TabLink key={tab.href} tab={tab} active={isActive(tab.href)} stacked />
        ))}
      </nav>
    </div>
  );
}

function TabLink({ tab, active, stacked }: { tab: Tab; active: boolean; stacked?: boolean }) {
  const { href, label, Icon } = tab;
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`flex items-center justify-center rounded-lg text-sm font-semibold transition ${
        stacked ? "flex-col gap-0.5 px-1 py-2 text-[11px]" : "gap-2 px-3 py-2"
      } ${active ? "text-primary" : "text-muted hover:text-fg"} ${active && !stacked ? "bg-primary/10" : ""}`}
    >
      <Icon width={stacked ? 22 : 18} height={stacked ? 22 : 18} />
      <span className="truncate">{label}</span>
    </Link>
  );
}
