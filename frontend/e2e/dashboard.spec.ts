import { expect, test as base, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";

interface State {
  cookie: string;
  ids: Record<string, number>;
}

const readState = (): State => JSON.parse(fs.readFileSync("e2e/.state.json", "utf8"));

// A page that is signed in as the seeded test user.
const test = base.extend<{ app: Page }>({
  app: async ({ context, page }, provide) => {
    await context.addCookies([{ name: "session", value: readState().cookie, url: "http://localhost:3100" }]);
    // An uncaught error in the page is a bug even if the assertions still pass.
    const crashes: string[] = [];
    page.on("pageerror", (err) => crashes.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error" && !/Failed to load resource/.test(msg.text())) console.log("[browser]", msg.text());
    });
    await provide(page);
    expect(crashes, "uncaught errors in the page").toEqual([]);
  },
});


const card = (page: Page, title: string): Locator =>
  page.getByTestId("task-card").filter({ hasText: title });

/** "YYYY-MM-DDTHH:mm" for `days` from now, as an India wall-clock time (what the form expects). */
function istInput(days: number): string {
  return new Date(Date.now() + days * 86_400_000 + 5.5 * 3_600_000).toISOString().slice(0, 16);
}

async function api<T>(page: Page, path: string): Promise<T> {
  const res = await page.request.get(`/api${path}`);
  expect(res.ok(), `${path} -> ${res.status()}`).toBeTruthy();
  return (await res.json()) as T;
}

type ApiTask = { id: number; title: string; status: string; priority: string; link: string | null; deadline: string | null };

/** A tab's badge. The bar is rendered twice (header and bottom bar); only one is visible. */
const badge = (page: Page, key: "review" | "suspicious") => page.locator(`[data-testid="count-${key}"]:visible`);

async function waitForTasks(page: Page) {
  await expect(page.getByRole("heading", { name: "Your tasks" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
}

// ------------------------------------------------------------------ login

test("a visitor who is not signed in is sent to the login page, which links to Google", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Smart To-Do" })).toBeVisible();

  const signIn = page.getByRole("link", { name: "Sign in with Google" });
  await expect(signIn).toHaveAttribute("href", "/api/auth/login");

  // Don't leave the machine: stand in for Google and check the backend sent us there properly.
  await page.route("https://accounts.google.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>google</h1>" }),
  );
  await signIn.click();
  await page.waitForURL(/accounts\.google\.com/);
  const url = new URL(page.url());
  expect(url.searchParams.get("client_id")).toBe("e2e.apps.googleusercontent.com");
  expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url.searchParams.get("scope")).toContain("gmail.readonly");
});

test("a signed-in visitor on the login page goes straight to the dashboard", async ({ app }) => {
  await app.goto("/login");
  await expect(app).toHaveURL(/localhost:3100\/$/);
  await waitForTasks(app);
});

// ------------------------------------------------------------------ task list

test("tasks are grouped Urgent, Important and Regular with source badge, IST deadline and countdown", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);

  const headings = await app.getByRole("heading", { level: 2 }).allTextContents();
  const groups = headings.filter((h) => /^(Urgent|Important|Regular)/.test(h)).map((h) => h.replace(/\d+$/, ""));
  expect(groups).toEqual(["Urgent", "Important", "Regular"]);

  const nptel = card(app, "Submit NPTEL Assignment 5");
  await expect(nptel.getByText("NPTEL", { exact: true })).toBeVisible();
  await expect(nptel).toContainText(/Due in 1d [56]h/);
  await expect(nptel).toContainText("IST");
  await expect(app.locator("section", { has: app.getByRole("heading", { name: /^Urgent/ }) })).toContainText("Submit NPTEL Assignment 5");
  await expect(app.locator("section", { has: app.getByRole("heading", { name: /^Important/ }) })).toContainText("hackathon registration form");
  await expect(card(app, "Fill the hackathon").getByText("Google Form")).toBeVisible();
});

test('"Open & Complete" opens the correct link in a new tab, with its domain shown beneath', async ({ app, context }) => {
  await context.route("https://onlinecourses.nptel.ac.in/**", (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<h1>NPTEL assignment</h1>" }),
  );
  await app.goto("/");
  await waitForTasks(app);

  const nptel = card(app, "Submit NPTEL Assignment 5");
  const button = nptel.getByRole("link", { name: "Open & Complete" });
  await expect(button).toHaveAttribute("target", "_blank");
  await expect(button).toHaveAttribute("rel", /noopener/);
  await expect(nptel.getByText("onlinecourses.nptel.ac.in")).toBeVisible();

  const [tab] = await Promise.all([context.waitForEvent("page"), button.click()]);
  await tab.waitForLoadState();
  expect(tab.url()).toBe("https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/5");
  await expect(tab.getByRole("heading", { name: "NPTEL assignment" })).toBeVisible();
  await tab.close();
  await expect(app).toHaveURL(/localhost:3100\/$/); // the dashboard stays where it was
});

test('a Gmail fallback link is labelled "Open email"; a task without a link says so', async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const circular = card(app, "Read the exam circular");
  const link = circular.getByRole("link", { name: "Open email" });
  await expect(link).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/seedcircular");
  await expect(circular.getByText("mail.google.com")).toBeVisible();
  await expect(circular.getByRole("link", { name: "Open & Complete" })).toHaveCount(0);
  await expect(card(app, "Edit me")).toContainText("No link");
  await expect(card(app, "Edit me").getByRole("link")).toHaveCount(0);
});

// ------------------------------------------------------------------ create / edit / done / delete

test("create a task with a deadline and a link, then edit it", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);

  await app.getByRole("button", { name: "Add task" }).click();
  const dialog = app.getByRole("dialog", { name: "Add a task" });
  await dialog.getByLabel("Title").fill("E2E created task");
  await dialog.getByLabel("Deadline (India time)").fill(istInput(6));
  await dialog.getByLabel("Link to complete it").fill("https://example.com/e2e-complete");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toHaveCount(0);

  const created = card(app, "E2E created task");
  await expect(created).toBeVisible();
  await expect(created.getByRole("link", { name: "Open & Complete" })).toHaveAttribute("href", "https://example.com/e2e-complete");
  await expect(created).toContainText("Due in 5 days");
  const [stored] = (await api<ApiTask[]>(app, "/tasks?source=manual")).filter((t) => t.title === "E2E created task");
  expect(stored).toMatchObject({ status: "open", priority: "regular", link: "https://example.com/e2e-complete" });

  // edit: rename, raise priority, change the link
  await created.getByRole("button", { name: "Edit E2E created task" }).click();
  const edit = app.getByRole("dialog", { name: "Edit task" });
  await expect(edit.getByLabel("Title")).toHaveValue("E2E created task");
  await edit.getByLabel("Title").fill("E2E renamed task");
  await edit.getByLabel("Priority").selectOption("important");
  await edit.getByLabel("Link to complete it").fill("https://example.com/changed");
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(edit).toHaveCount(0);

  const renamed = card(app, "E2E renamed task");
  await expect(renamed).toBeVisible();
  await expect(card(app, "E2E created task")).toHaveCount(0);
  await expect(renamed.getByRole("link", { name: "Open & Complete" })).toHaveAttribute("href", "https://example.com/changed");
  await expect(app.locator("section", { has: app.getByRole("heading", { name: /^Important/ }) })).toContainText("E2E renamed task");
  const after = (await api<ApiTask[]>(app, "/tasks?source=manual")).find((t) => t.id === stored.id);
  expect(after).toMatchObject({ title: "E2E renamed task", priority: "important", link: "https://example.com/changed" });
});

test("clearing the deadline and link of a task in the edit form saves", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  await card(app, "E2E renamed task").getByRole("button", { name: /^Edit/ }).click();
  const edit = app.getByRole("dialog", { name: "Edit task" });
  await edit.getByLabel("Deadline (India time)").fill("");
  await edit.getByLabel("Link to complete it").fill("");
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(edit).toHaveCount(0);
  const renamed = card(app, "E2E renamed task");
  await expect(renamed).toContainText("No deadline");
  await expect(renamed).toContainText("No link");
});

test("the form rejects an empty title and a non-http link without saving", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const before = (await api<ApiTask[]>(app, "/tasks?limit=500")).length;

  await app.getByRole("button", { name: "Add task" }).click();
  const dialog = app.getByRole("dialog", { name: "Add a task" });
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Give the task a title");

  await dialog.getByLabel("Title").fill("Bad link task");
  await dialog.getByLabel("Link to complete it").fill("javascript:alert(1)");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog.getByRole("alert")).toContainText("http://");
  await expect(dialog).toBeVisible();

  await app.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect((await api<ApiTask[]>(app, "/tasks?limit=500")).length).toBe(before);
});

test("mark done removes the task, Undo brings it back, and it shows under completed", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const row = card(app, "Mark me done");
  await row.getByRole("button", { name: "Done", exact: true }).click();
  await expect(row).toHaveCount(0);
  const toast = app.getByRole("status").filter({ hasText: "Marked as done" });
  await expect(toast).toBeVisible();

  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(card(app, "Mark me done")).toBeVisible();
  expect((await api<ApiTask[]>(app, "/tasks?status=open&limit=500")).some((t) => t.title === "Mark me done")).toBe(true);

  await card(app, "Mark me done").getByRole("button", { name: "Done", exact: true }).click();
  await expect(card(app, "Mark me done")).toHaveCount(0);
  const done = await api<ApiTask[]>(app, "/tasks?status=done");
  expect(done.map((t) => t.title)).toContain("Mark me done");

  await app.getByRole("button", { name: "Show completed tasks" }).click();
  await expect(card(app, "Mark me done")).toBeVisible();
  await expect(card(app, "Mark me done").getByRole("button", { name: "Reopen" })).toBeVisible();
});

test("delete asks first, cancel keeps the task, confirm removes it", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const row = card(app, "Delete me");
  await row.getByRole("button", { name: "Delete Delete me" }).click();
  const confirm = app.getByRole("dialog", { name: "Delete this task?" });
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: "Delete Delete me" }).click();
  await app.getByRole("dialog", { name: "Delete this task?" }).getByRole("button", { name: "Delete" }).click();
  await expect(row).toHaveCount(0);
  const res = await app.request.get(`/api/tasks/${readState().ids.delete}`);
  expect(res.status()).toBe(404);
});

// ------------------------------------------------------------------ notification bell

test("the bell shows the unread count, opening a notification marks it read and opens its task, mark all read clears it", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const count = app.getByTestId("bell-count");
  await expect(count).toHaveText("3");

  await app.getByRole("button", { name: /Notifications, 3 unread/ }).click();
  const panel = app.getByRole("region", { name: "Notifications" });
  await expect(panel.getByRole("listitem")).toHaveCount(4);
  await expect(panel).toContainText("Daily digest");

  await panel.getByRole("button", { name: /Complete immediately: Submit NPTEL Assignment 5/ }).click();
  await expect(app).toHaveURL(/localhost:3100\/$/); // ?task=ID was consumed
  await expect(count).toHaveText("2");
  await expect(card(app, "Submit NPTEL Assignment 5")).toHaveClass(/flash/);
  const afterOne = await api<{ unread_count: number }>(app, "/notifications");
  expect(afterOne.unread_count).toBe(2);

  await app.getByRole("button", { name: /Notifications, 2 unread/ }).click();
  await panel.getByRole("button", { name: "Mark all read" }).click();
  await expect(count).toHaveCount(0);
  expect((await api<{ unread_count: number }>(app, "/notifications")).unread_count).toBe(0);
  await app.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
});

test("a notification for a task that is already done opens that task", async ({ app }) => {
  await app.goto(`/?task=${readState().ids.done}`);
  const dialog = app.getByRole("dialog", { name: "Edit task" });
  await expect(dialog.getByLabel("Title")).toHaveValue("Mark me done");
  await app.keyboard.press("Escape");
});

// ------------------------------------------------------------------ needs review

test("needs review: confirm, edit and confirm, and dismiss", async ({ app }) => {
  await app.goto("/review");
  await expect(app.getByRole("heading", { name: "Needs review" })).toBeVisible();
  await expect(badge(app, "review")).toHaveText("3");
  const cards = app.getByTestId("review-card");
  await expect(cards).toHaveCount(3);
  await expect(cards.first()).toContainText("none found");

  // confirm as-is: it leaves the list and shows up among the tasks
  await cards.filter({ hasText: "Workshop invite" }).getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(cards.filter({ hasText: "Workshop invite" })).toHaveCount(0);
  await expect(badge(app, "review")).toHaveText("2");

  // edit then confirm
  await cards.filter({ hasText: "Seminar notice" }).getByRole("button", { name: /Edit & confirm/ }).click();
  const dialog = app.getByRole("dialog", { name: "Edit and confirm" });
  await expect(dialog.getByLabel("Priority")).toHaveCount(0);
  await dialog.getByLabel("Title").fill("Seminar on 5G (confirmed)");
  await dialog.getByLabel("Deadline (India time)").fill(istInput(4));
  await dialog.getByRole("button", { name: "Confirm task" }).click();
  await expect(cards.filter({ hasText: "Seminar" })).toHaveCount(0);

  // dismiss
  await cards.filter({ hasText: "Newsletter digest" }).getByRole("button", { name: "Dismiss" }).click();
  await expect(cards).toHaveCount(0);
  await expect(app.getByText("Nothing to review")).toBeVisible();
  await expect(badge(app, "review")).toHaveCount(0);

  await app.getByRole("link", { name: "Tasks" }).first().click();
  await waitForTasks(app);
  await expect(card(app, "Workshop invite")).toBeVisible();
  await expect(card(app, "Seminar on 5G (confirmed)")).toBeVisible();
  await expect(card(app, "Seminar on 5G (confirmed)")).toContainText("Due in 3 days");
  await expect(card(app, "Newsletter digest")).toHaveCount(0);
  const tasks = await api<ApiTask[]>(app, "/tasks?limit=500");
  expect(tasks.find((t) => t.title === "Workshop invite (confirm me)")?.status).toBe("open");
  expect((await api<ApiTask[]>(app, "/review")).length).toBe(0);
});

// ------------------------------------------------------------------ suspicious

test("suspicious: shows why each email was held; rescue creates a task, confirm scam closes one", async ({ app }) => {
  await app.goto("/suspicious");
  await expect(app.getByRole("heading", { name: "Suspicious" })).toBeVisible();
  const items = app.getByTestId("suspicious-card");
  await expect(items).toHaveCount(2);
  await expect(badge(app, "suspicious")).toHaveText("2");
  const first = items.filter({ hasText: "NPTEL certificate fee" });
  await expect(first).toContainText("Asks for a registration, processing or similar fee");
  await expect(first).toContainText("nptel.help@gmail.com");
  await expect(first.getByRole("link")).toHaveCount(0); // never offers a link into a suspicious email

  await first.getByRole("button", { name: /Rescue/ }).click();
  await expect(app.getByRole("status").filter({ hasText: "Task created" })).toBeVisible();
  await expect(items).toHaveCount(1);

  await items.getByRole("button", { name: "Confirm scam" }).click();
  await expect(items).toHaveCount(0);
  await expect(app.getByText("Nothing suspicious")).toBeVisible();
  await expect(badge(app, "suspicious")).toHaveCount(0);

  await app.getByRole("link", { name: "Tasks" }).first().click();
  await waitForTasks(app);
  const rescued = card(app, "NPTEL: Week 5 quiz is live");
  await expect(rescued).toBeVisible();
  await expect(rescued.getByRole("link", { name: "Open & Complete" })).toHaveAttribute(
    "href",
    "https://onlinecourses.nptel.ac.in/noc26_cs10/unit?unit=5&assessment=20",
  );
  const held = await api<{ id: number }[]>(app, "/quarantine");
  expect(held).toEqual([]);
  expect((await api<{ status: string }[]>(app, "/quarantine?status=confirmed_scam")).length).toBe(1);
});

// ------------------------------------------------------------------ settings

test("settings: edit the filter lists and digest hour, reject bad entries, and persist after reload", async ({ app }) => {
  await app.goto("/settings");
  await expect(app.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(app.getByText("e2e@example.test")).toBeVisible();
  const save = app.getByRole("button", { name: "Save settings" });
  await expect(save).toBeDisabled();

  const deny = app.getByRole("textbox", { name: "Never scan mail from" });
  await expect(app.getByRole("list", { name: "Never scan mail from entries" })).toContainText("linkedin.com");
  await deny.fill("javascript:alert(1)");
  await app.getByRole("button", { name: "Add to Never scan mail from" }).click();
  await expect(app.locator('p[role="alert"]')).toContainText("plain domain");
  await deny.fill("@Spammy.Example");
  await deny.press("Enter");
  await expect(app.getByRole("list", { name: "Never scan mail from entries" })).toContainText("spammy.example");

  await app.getByRole("textbox", { name: "My college domains" }).fill("college.example.edu");
  await app.getByRole("button", { name: "Add to My college domains" }).click();
  await app.getByLabel("Remove naukri.com").click();
  await app.getByLabel("Daily digest time").selectOption("20");
  await expect(save).toBeEnabled();
  await save.click();
  await expect(app.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
  await expect(save).toBeDisabled();

  await app.reload();
  await expect(app.getByRole("list", { name: "Never scan mail from entries" })).toContainText("spammy.example");
  await expect(app.getByRole("list", { name: "Never scan mail from entries" })).not.toContainText("naukri.com");
  await expect(app.getByRole("list", { name: "My college domains entries" })).toContainText("college.example.edu");
  await expect(app.getByLabel("Daily digest time")).toHaveValue("20");
  expect(await api(app, "/settings")).toEqual({
    denylist: ["linkedin.com", "spammy.example"],
    allowlist: ["college.example.edu"],
    always_track: [],
    digest_hour: 20,
  });
});

test("always turn into tasks: add, reject bad or conflicting entries, remove, and persist after reload", async ({ app }) => {
  await app.goto("/settings");
  const tracked = app.getByRole("textbox", { name: "Always turn into tasks" });
  const list = app.getByRole("list", { name: "Always turn into tasks entries" });
  const add = app.getByRole("button", { name: "Add to Always turn into tasks" });
  const alert = app.locator('p[role="alert"]');
  await expect(tracked).toBeVisible(); // settings have loaded

  // the section sits above "Never scan mail from" and explains itself
  const headings = await app.getByText(/^(Always turn into tasks|Never scan mail from|My college domains)$/).allTextContents();
  expect(headings).toEqual(["Always turn into tasks", "Never scan mail from", "My college domains"]);
  await expect(app.getByText("Mail from these senders always becomes a task, even from the Promotions tab.")).toBeVisible();
  await expect(list).toHaveCount(0); // nothing yet

  for (const bad of ["nodot", "https://nptel.ac.in", "a b.com", "x@", "-bad.com"]) {
    await tracked.fill(bad);
    await add.click();
    await expect(alert).toContainText("domain");
    await expect(list).toHaveCount(0);
  }

  await tracked.fill("spammy.example"); // already in "Never scan mail from"
  await add.click();
  await expect(alert).toContainText('already in "Never scan mail from"');
  await expect(list).toHaveCount(0);

  await tracked.fill("NPTEL.ac.in");
  await tracked.press("Enter");
  await tracked.fill("News@Example.com");
  await add.click();
  await expect(list).toContainText("nptel.ac.in");
  await expect(list).toContainText("news@example.com");
  await expect(alert).toHaveCount(0);

  // the same entry cannot also be denylisted
  const deny = app.getByRole("textbox", { name: "Never scan mail from" });
  await deny.fill("nptel.ac.in");
  await app.getByRole("button", { name: "Add to Never scan mail from" }).click();
  await expect(alert).toContainText('already in "Always turn into tasks"');

  await app.getByLabel("Remove news@example.com").click();
  await expect(list).not.toContainText("news@example.com");
  await app.getByRole("button", { name: "Save settings" }).click();
  await expect(app.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();

  await app.reload();
  await expect(app.getByRole("list", { name: "Always turn into tasks entries" })).toContainText("nptel.ac.in");
  await expect(app.getByRole("list", { name: "Always turn into tasks entries" })).not.toContainText("news@example.com");
  expect((await api<{ always_track: string[] }>(app, "/settings")).always_track).toEqual(["nptel.ac.in"]);

  // removing the last chip persists too
  await app.getByLabel("Remove nptel.ac.in").click();
  await app.getByRole("button", { name: "Save settings" }).click();
  await expect(app.getByRole("status").filter({ hasText: "Settings saved" }).last()).toBeVisible();
  await app.reload();
  await expect(app.getByRole("list", { name: "Always turn into tasks entries" })).toHaveCount(0);
  expect((await api<{ always_track: string[] }>(app, "/settings")).always_track).toEqual([]);
});

test('tasks with no deadline show a "No deadline" badge; tracked tasks are labelled Tracked', async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  const tracked = card(app, "Statement ready (tracked sender)");
  await expect(tracked.getByTestId("no-deadline")).toHaveText("No deadline");
  await expect(tracked.getByText("Tracked", { exact: true })).toBeVisible();
  await expect(app.locator("section", { has: app.getByRole("heading", { name: /^Important/ }) })).toContainText("Statement ready (tracked sender)");
  await expect(card(app, "Edit me").getByTestId("no-deadline")).toBeVisible();
  // tasks that do have a deadline never show the badge
  await expect(card(app, "Submit NPTEL Assignment 5").getByTestId("no-deadline")).toHaveCount(0);
  await expect(card(app, "Read the exam circular").getByTestId("no-deadline")).toHaveCount(0);
});

test("push opt-in: enable, send a test notification, see it in the bell, turn it off", async ({ app }) => {
  // Headless Chromium has no push service to subscribe to, so stand in for the browser's
  // PushManager. Everything else (permission, API calls, backend) is the real app.
  await app.addInitScript(() => {
    const w = window as unknown as { __sub: unknown };
    w.__sub = null;
    const sub = {
      endpoint: `https://fcm.example.test/send/e2e-${Math.random().toString(36).slice(2)}`,
      expirationTime: null,
      toJSON() {
        return { endpoint: this.endpoint, expirationTime: null, keys: { p256dh: "BPe2eKey", auth: "e2eAuth" } };
      },
      async unsubscribe() {
        w.__sub = null;
        return true;
      },
    };
    PushManager.prototype.getSubscription = async () => w.__sub as PushSubscription | null;
    PushManager.prototype.subscribe = async () => {
      w.__sub = sub;
      return sub as unknown as PushSubscription;
    };
  });

  const unreadBefore = (await api<{ unread_count: number }>(app, "/notifications")).unread_count;
  await app.goto("/settings");
  await expect(app.getByTestId("push-status")).toHaveAttribute("data-state", "off");
  await app.getByRole("button", { name: "Enable push notifications" }).click();
  await expect(app.getByTestId("push-status")).toHaveAttribute("data-state", "on");

  await app.getByRole("button", { name: "Send test notification" }).click();
  await expect(app.getByRole("status").filter({ hasText: "Test notification sent" })).toBeVisible();
  const pushes = await (await app.request.get("http://127.0.0.1:8001/__e2e/pushes")).json();
  expect(pushes).toEqual([
    { title: "Test notification", body: "Push notifications are working.", link: "http://localhost:3100" },
  ]);

  const unread = String(unreadBefore + 1);
  await expect(app.getByTestId("bell-count")).toHaveText(unread);
  await app.getByRole("button", { name: new RegExp(`Notifications, ${unread} unread`) }).click();
  await expect(app.getByRole("region", { name: "Notifications" })).toContainText("Test notification");
  await app.keyboard.press("Escape");

  await app.getByRole("button", { name: "Turn off push" }).click();
  await expect(app.getByTestId("push-status")).toHaveAttribute("data-state", "off");
});

// ------------------------------------------------------------------ theme and layout

test("dark mode: follows the system, can be toggled, and is remembered", async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: "dark", baseURL: "http://localhost:3100" });
  await context.addCookies([{ name: "session", value: readState().cookie, url: "http://localhost:3100" }]);
  const page = await context.newPage();
  await page.goto("/");
  await waitForTasks(page);
  await expect(page.locator("html")).toHaveClass(/dark/);
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(11, 16, 32)");

  await page.getByRole("button", { name: "Switch to light mode" }).click();
  await expect(page.locator("html")).not.toHaveClass(/dark/);
  await page.reload();
  await waitForTasks(page);
  await expect(page.locator("html")).not.toHaveClass(/dark/); // the choice beat the system setting
  await context.close();
});

test("at 380px wide every page is usable: nothing overflows and the controls are reachable", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 380, height: 800 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    baseURL: "http://localhost:3100",
  });
  await context.addCookies([{ name: "session", value: readState().cookie, url: "http://localhost:3100" }]);
  const page = await context.newPage();
  fs.mkdirSync("e2e/screenshots", { recursive: true });

  for (const [path, name] of [["/", "tasks"], ["/review", "review"], ["/suspicious", "suspicious"], ["/settings", "settings"]]) {
    await page.goto(path);
    await expect(page.getByRole("status", { name: /Loading/ })).toHaveCount(0);
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `e2e/screenshots/mobile-380-${name}.png` });
  }

  await page.goto("/");
  await waitForTasks(page);
  const nav = page.getByRole("navigation", { name: "Main" }).last();
  await expect(nav).toBeVisible();
  for (const label of ["Tasks", "Needs review", "Suspicious", "Settings"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  // the primary action is a full-width, thumb-sized button
  const open = card(page, "Submit NPTEL Assignment 5").getByRole("link", { name: "Open & Complete" });
  const box = (await open.boundingBox())!;
  expect(box.height).toBeGreaterThanOrEqual(44);
  expect(box.width).toBeGreaterThan(280);

  await page.getByRole("button", { name: /Notifications/ }).click();
  const panel = page.getByRole("region", { name: "Notifications" });
  const panelBox = (await panel.boundingBox())!;
  expect(panelBox.x).toBeGreaterThanOrEqual(0);
  expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(380);
  await context.close();
});

// ------------------------------------------------------------------ PWA

test("the app is installable: manifest, icons, service worker and no installability errors", async ({ app, context }) => {
  await app.goto("/");
  await waitForTasks(app);

  const manifestHref = await app.locator('link[rel="manifest"]').getAttribute("href");
  expect(manifestHref).toBeTruthy();
  const manifest = await (await app.request.get(manifestHref!)).json();
  expect(manifest).toMatchObject({ name: "Smart To-Do", display: "standalone", start_url: "/", scope: "/" });
  const sizes = manifest.icons.map((i: { sizes: string; purpose: string }) => `${i.sizes}:${i.purpose}`);
  expect(sizes).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));
  for (const icon of manifest.icons as { src: string }[]) {
    const res = await app.request.get(icon.src);
    expect(res.status(), icon.src).toBe(200);
    expect(res.headers()["content-type"]).toContain("image/png");
  }

  // the service worker registers, activates and controls the whole site
  const scope = await app.evaluate(async () => {
    const reg = await navigator.serviceWorker.ready;
    return reg.scope;
  });
  expect(scope).toBe("http://localhost:3100/");
  expect(context.serviceWorkers().length).toBeGreaterThan(0);
  const cacheHeader = (await app.request.get("/sw.js")).headers()["cache-control"];
  expect(cacheHeader).toContain("no-cache");

  const cdp = await context.newCDPSession(app);
  const { installabilityErrors } = (await cdp.send("Page.getInstallabilityErrors")) as {
    installabilityErrors: { errorId: string }[];
  };
  // Playwright runs every context like an incognito window, which Chromium reports as its own
  // "error"; anything else here (manifest, icons, service worker, https/localhost) is a real problem.
  expect(installabilityErrors.filter((e) => e.errorId !== "in-incognito")).toEqual([]);
});

test("the service worker shows a pushed notification that carries the task's link, and offers an offline page", async ({ app, context }) => {
  await app.goto("/");
  await waitForTasks(app);
  await app.evaluate(() => navigator.serviceWorker.ready);

  // The worker must be active before a push can reach it.
  await expect
    .poll(() => app.evaluate(async () => (await navigator.serviceWorker.ready).active?.state))
    .toBe("activated");

  const cdp = await context.newCDPSession(app);
  let registrationId: string | undefined;
  cdp.on("ServiceWorker.workerRegistrationUpdated", (e: { registrations: { registrationId: string; scopeURL: string }[] }) => {
    registrationId = e.registrations.find((r) => r.scopeURL === "http://localhost:3100/")?.registrationId ?? registrationId;
  });
  await cdp.send("ServiceWorker.enable");
  await expect.poll(() => registrationId).toBeTruthy();

  const payload = {
    title: "Complete immediately: Submit NPTEL Assignment 5",
    body: "Due Tue, 22 Jan 2030, 11:59 PM IST",
    url: "https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/5",
    tag: "task-1",
    task_id: 1,
  };
  const shownNow = () =>
    app.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      return (await reg.getNotifications()).map((n) => ({ title: n.title, body: n.body, url: (n.data as { url: string }).url }));
    });
  // Deliver until it shows. The same tag replaces the previous notification, so retries never pile up.
  await expect
    .poll(
      async () => {
        await cdp.send("ServiceWorker.deliverPushMessage", {
          origin: "http://localhost:3100",
          registrationId: registrationId!,
          data: JSON.stringify(payload),
        });
        return (await shownNow()).length;
      },
      { timeout: 20_000, intervals: [500] },
    )
    .toBe(1);
  const shown = await shownNow();
  expect(shown).toEqual([{ title: payload.title, body: payload.body, url: payload.url }]);

  // offline: a navigation falls back to the cached offline page instead of a browser error
  await context.setOffline(true);
  await app.goto("/review").catch(() => {});
  await expect(app.getByRole("heading", { name: "You're offline" })).toBeVisible();
  await context.setOffline(false);
});

// ------------------------------------------------------------------ account

test("sign out returns to the login page and the session no longer works", async ({ app }) => {
  await app.goto("/settings");
  await app.getByRole("button", { name: "Sign out" }).click();
  await expect(app).toHaveURL(/\/login$/);
  await app.goto("/");
  await expect(app).toHaveURL(/\/login$/);
});

test('"Delete my data" needs DELETE typed, then removes the account and signs out', async ({ app, context }) => {
  await context.addCookies([{ name: "session", value: readState().cookie, url: "http://localhost:3100" }]);
  await app.goto("/settings");
  await app.getByRole("button", { name: "Delete my data" }).first().click();
  const dialog = app.getByRole("dialog", { name: "Delete everything?" });
  const confirm = dialog.getByRole("button", { name: "Delete everything" });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("Type DELETE to confirm").fill("delete");
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel("Type DELETE to confirm").fill("DELETE");
  await confirm.click();
  await expect(app).toHaveURL(/\/login$/);

  await context.addCookies([{ name: "session", value: readState().cookie, url: "http://localhost:3100" }]);
  const me = await app.request.get("/api/me");
  expect(me.status()).toBe(401); // the user is gone, so the old cookie is worthless
});
