import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { describe, expect, it } from "vitest";

// Runs the real public/sw.js against a stand-in for the worker scope, so the push and
// notification-click behaviour is tested without a push service or a real notification click.

type Handler = (event: unknown) => void;

function loadWorker() {
  const source = readFileSync(path.join(process.cwd(), "public", "sw.js"), "utf8");
  const handlers: Record<string, Handler> = {};
  const opened: string[] = [];
  const shown: { title: string; options: Record<string, unknown> }[] = [];
  const self = {
    addEventListener: (type: string, fn: Handler) => {
      handlers[type] = fn;
    },
    skipWaiting: () => {},
    registration: {
      showNotification: (title: string, options: Record<string, unknown>) => {
        shown.push({ title, options });
        return Promise.resolve();
      },
    },
    clients: {
      claim: () => Promise.resolve(),
      openWindow: (url: string) => {
        opened.push(url);
        return Promise.resolve({ focus: () => Promise.resolve() });
      },
    },
  };
  const caches = { open: () => Promise.resolve({ add: () => Promise.resolve() }), keys: () => Promise.resolve([]) };
  vm.runInNewContext(source, { self, caches, fetch: () => Promise.reject(new Error("offline")) });

  const waits: Promise<unknown>[] = [];
  const dispatch = async (type: string, event: Record<string, unknown>) => {
    handlers[type]({ ...event, waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);
  };
  return { dispatch, opened, shown };
}

const notificationWith = (url: unknown) => {
  const state = { closed: false };
  return { state, notification: { close: () => (state.closed = true), data: { url } } };
};

describe("service worker: push", () => {
  it("shows the pushed title and body and keeps the task link on the notification", async () => {
    const sw = loadWorker();
    const payload = {
      title: "Complete immediately: Quiz",
      body: "Due Tue, 22 Jan 2030, 11:59 PM IST",
      url: "https://onlinecourses.nptel.ac.in/noc/assignment/5",
      tag: "task-7",
      task_id: 7,
    };
    await sw.dispatch("push", { data: { json: () => payload, text: () => "" } });
    expect(sw.shown).toHaveLength(1);
    expect(sw.shown[0].title).toBe(payload.title);
    expect(sw.shown[0].options).toMatchObject({
      body: payload.body,
      tag: "task-7",
      data: { url: payload.url, taskId: 7 },
    });
  });

  it("still shows something for a malformed or empty push", async () => {
    const sw = loadWorker();
    await sw.dispatch("push", {
      data: {
        json: () => {
          throw new Error("not json");
        },
        text: () => "plain text",
      },
    });
    await sw.dispatch("push", { data: null });
    expect(sw.shown.map((s) => s.title)).toEqual(["Reminder", "Reminder"]);
    expect(sw.shown[0].options.body).toBe("plain text");
  });
});

describe("service worker: clicking a notification", () => {
  it("opens the task's action link and closes the notification", async () => {
    const sw = loadWorker();
    const { notification, state } = notificationWith("https://forms.gle/Hackathon26");
    await sw.dispatch("notificationclick", { notification });
    expect(sw.opened).toEqual(["https://forms.gle/Hackathon26"]);
    expect(state.closed).toBe(true);
  });

  it("opens an http link too", async () => {
    const sw = loadWorker();
    await sw.dispatch("notificationclick", notificationWith("http://localhost:3000/"));
    expect(sw.opened).toEqual(["http://localhost:3000/"]);
  });

  it.each([["javascript:alert(1)"], ["data:text/html,x"], ["file:///etc/passwd"], [""], [undefined], [42]])(
    "never opens %s",
    async (url) => {
      const sw = loadWorker();
      const { notification, state } = notificationWith(url);
      await sw.dispatch("notificationclick", { notification });
      expect(sw.opened).toEqual([]);
      expect(state.closed).toBe(true);
    },
  );
});

describe("service worker: offline", () => {
  it("falls back to the cached offline page when a navigation fails", async () => {
    const source = readFileSync(path.join(process.cwd(), "public", "sw.js"), "utf8");
    const handlers: Record<string, Handler> = {};
    const self = { addEventListener: (t: string, fn: Handler) => (handlers[t] = fn), skipWaiting: () => {} };
    const caches = { match: (url: string) => Promise.resolve(`cached:${url}`) };
    vm.runInNewContext(source, { self, caches, fetch: () => Promise.reject(new Error("offline")) });

    let response: Promise<unknown> | undefined;
    handlers.fetch({ request: { mode: "navigate" }, respondWith: (p: Promise<unknown>) => (response = p) });
    expect(await response).toBe("cached:/offline.html");

    response = undefined; // anything that is not a page load is left to the network
    handlers.fetch({ request: { mode: "cors" }, respondWith: (p: Promise<unknown>) => (response = p) });
    expect(response).toBeUndefined();
  });
});
