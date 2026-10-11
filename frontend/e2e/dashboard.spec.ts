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
const badge = (page: Page, key: "attention") => page.locator(`[data-testid="count-${key}"]:visible`);

async function waitForTasks(page: Page, title = "My Tasks") {
  await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
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
  await app.goto("/email");
  await waitForTasks(app, "From Email");

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
  await app.goto("/email");
  await waitForTasks(app, "From Email");

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
  await expect(app).toHaveURL(/localhost:3100\/email$/); // the dashboard stays where it was
});

test('a Gmail fallback link is labelled "Open email"; a task without a link says so', async ({ app }) => {
  await app.goto("/email");
  await waitForTasks(app, "From Email");
  const circular = card(app, "Read the exam circular");
  const link = circular.getByRole("link", { name: "Open email" });
  await expect(link).toHaveAttribute("href", "https://mail.google.com/mail/u/0/#all/seedcircular");
  await expect(circular.getByText("mail.google.com")).toBeVisible();
  await expect(circular.getByRole("link", { name: "Open & Complete" })).toHaveCount(0);
  await app.goto("/");
  await waitForTasks(app);
  await expect(card(app, "Edit me")).toContainText("No link");
  await expect(card(app, "Edit me").getByRole("link")).toHaveCount(0);
});

// ------------------------------------------------------------------ create / edit / done / delete

test("create a task with a deadline and a link, then edit it", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);

  await app.getByRole("button", { name: "New task" }).click();
  const dialog = app.getByRole("dialog", { name: "New task", exact: true });
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

  await app.getByRole("button", { name: "New task" }).click();
  const dialog = app.getByRole("dialog", { name: "New task", exact: true });
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
  await expect(app).toHaveURL(/localhost:3100\/email$/); // sent to the email tab, ?task=ID consumed
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
  await app.goto("/attention");
  const susp = (await api<unknown[]>(app, "/quarantine")).length;
  await expect(app.getByRole("heading", { name: "Needs review" })).toBeVisible();
  const cards = app.getByTestId("review-card");
  const start = (await api<ApiTask[]>(app, "/review")).length;
  await expect(badge(app, "attention")).toHaveText(String(start + susp));
  await expect(cards).toHaveCount(start);
  await expect(cards.filter({ hasText: "Workshop invite" })).toContainText("none found");

  // confirm as-is: it leaves the list and shows up among the tasks
  await cards.filter({ hasText: "Workshop invite" }).getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(cards.filter({ hasText: "Workshop invite" })).toHaveCount(0);
  await expect(badge(app, "attention")).toHaveText(String(start - 1 + susp));

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
  await expect(cards.filter({ hasText: "Newsletter digest" })).toHaveCount(0);
  await expect(cards).toHaveCount(start - 3);
  await expect(badge(app, "attention")).toHaveText(String(start - 3 + susp));

  await app.getByRole("link", { name: "From Email" }).first().click();
  await waitForTasks(app, "From Email");
  await expect(card(app, "Workshop invite")).toBeVisible();
  await expect(card(app, "Seminar on 5G (confirmed)")).toBeVisible();
  await expect(card(app, "Seminar on 5G (confirmed)")).toContainText("Due in 3 days");
  await expect(card(app, "Newsletter digest")).toHaveCount(0);
  const tasks = await api<ApiTask[]>(app, "/tasks?limit=500");
  expect(tasks.find((t) => t.title === "Workshop invite (confirm me)")?.status).toBe("open");
  expect((await api<ApiTask[]>(app, "/review")).length).toBe(start - 3);
});

test("needs review: confirm all and dismiss all from one sender", async ({ app }) => {
  await app.goto("/attention");
  const susp = (await api<unknown[]>(app, "/quarantine")).length;
  const groupA = app.getByRole("region", { name: "bulk-a@news.example.test" });
  const groupB = app.getByRole("region", { name: "bulk-b@promo.example.test" });
  await expect(groupA.getByTestId("review-card")).toHaveCount(3);
  await expect(groupB.getByTestId("review-card")).toHaveCount(2);
  const before = (await api<ApiTask[]>(app, "/review")).length;

  await groupA.getByRole("button", { name: "Confirm all from this sender" }).click();
  await expect(app.getByRole("status").filter({ hasText: "Confirmed 3 from bulk-a@news.example.test" })).toBeVisible();
  await expect(groupA).toHaveCount(0);
  await expect(badge(app, "attention")).toHaveText(String(before - 3 + susp));

  // dismissing a whole sender asks first
  await groupB.getByRole("button", { name: "Dismiss all from this sender" }).click();
  const dialog = app.getByRole("dialog", { name: "Dismiss 2 emails?" });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(groupB.getByTestId("review-card")).toHaveCount(2);
  await groupB.getByRole("button", { name: "Dismiss all from this sender" }).click();
  await app.getByRole("dialog", { name: "Dismiss 2 emails?" }).getByRole("button", { name: "Dismiss 2" }).click();
  await expect(groupB).toHaveCount(0);
  await expect(badge(app, "attention")).toHaveText(String(before - 5 + susp));

  const open = await api<ApiTask[]>(app, "/tasks?status=open&limit=500");
  expect(open.filter((t) => t.title.endsWith("(bulk confirm)")).length).toBe(3);
  const dismissed = await api<ApiTask[]>(app, "/tasks?status=dismissed&limit=500");
  expect(dismissed.filter((t) => t.title.endsWith("(bulk dismiss)")).length).toBe(2);
  expect((await api<ApiTask[]>(app, "/review")).length).toBe(before - 5);

  await app.getByRole("link", { name: "From Email" }).first().click();
  await waitForTasks(app, "From Email");
  await expect(card(app, "Course notice 1 (bulk confirm)")).toBeVisible();
  await expect(card(app, "Promo 1")).toHaveCount(0);
});

// ------------------------------------------------------------------ suspicious

test("suspicious: shows why each email was held; rescue creates a task, confirm scam closes one", async ({ app }) => {
  await app.goto("/attention");
  await expect(app.getByRole("heading", { name: "Suspicious" })).toBeVisible();
  const items = app.getByTestId("suspicious-card");
  await expect(items).toHaveCount(2);
  await expect(app.getByTestId("attention-link")).toBeVisible();
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
  
  await app.getByRole("link", { name: "From Email" }).first().click();
  await waitForTasks(app, "From Email");
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

test("sources: defaults, add and remove senders, add and remove a source, persists after reload", async ({ app }) => {
  await app.goto("/settings");
  await expect(app.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(app.getByText("e2e@example.test")).toBeVisible();
  const save = app.getByRole("button", { name: "Save settings" });
  const saved = app.getByRole("status").filter({ hasText: "Settings saved" });
  const alert = app.locator('p[role="alert"]');

  // one card per source, with its sender chips
  const cards = app.getByTestId("source-card");
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0)).toContainText("nptel.ac.in");
  await expect(cards.nth(1).getByRole("list", { name: "Digii senders entries" })).toContainText("digii.example");
  await expect(cards.nth(2)).toContainText("No senders yet");
  await expect(save).toBeDisabled();
  // the old sections are gone
  for (const gone of ["Never scan mail from", "My college domains", "Always turn into tasks"]) {
    await expect(app.getByText(gone, { exact: true })).toHaveCount(0);
  }

  // a sender chip: rejects bad entries, normalises good ones
  const internship = app.getByRole("textbox", { name: "Internship senders" });
  for (const bad of ["javascript:alert(1)", "nodot", "a b.com", "x OR from:boss@work.test"]) {
    await internship.fill(bad);
    await app.getByRole("button", { name: "Add to Internship senders" }).click();
    await expect(alert).toContainText("domain");
  }
  await internship.fill("Alerts@Jobs.Example");
  await internship.press("Enter");
  await expect(app.getByRole("list", { name: "Internship senders entries" })).toContainText("alerts@jobs.example");
  await expect(save).toBeEnabled();

  // a new source: name is required and unique
  const nameBox = app.getByLabel("New source name");
  await app.getByRole("button", { name: "Add source" }).click();
  await expect(alert).toContainText("Give the source a name");
  await nameBox.fill("nptel");
  await app.getByRole("button", { name: "Add source" }).click();
  await expect(alert).toContainText('already have a source called "nptel"');
  await nameBox.fill("Placement cell");
  await app.getByRole("button", { name: "Add source" }).click();
  await expect(cards).toHaveCount(4);
  await app.getByRole("textbox", { name: "Placement cell senders" }).fill("placement.college.example");
  await app.getByRole("button", { name: "Add to Placement cell senders" }).click();
  await app.getByLabel("Daily digest time").selectOption("20");
  await save.click();
  await expect(saved).toBeVisible();
  await expect(save).toBeDisabled();

  await app.reload();
  await expect(cards).toHaveCount(4);
  await expect(app.getByRole("list", { name: "Internship senders entries" })).toContainText("alerts@jobs.example");
  await expect(app.getByRole("list", { name: "Placement cell senders entries" })).toContainText("placement.college.example");
  await expect(app.getByLabel("Daily digest time")).toHaveValue("20");
  expect((await api<{ sources: { name: string; type: string; senders: string[] }[] }>(app, "/settings")).sources).toEqual([
    { name: "NPTEL", type: "nptel", senders: ["nptel.ac.in"] },
    { name: "Digii", type: "digii", senders: ["digii.example"] },
    { name: "Internship", type: "internship", senders: ["alerts@jobs.example"] },
    { name: "Placement cell", type: "custom", senders: ["placement.college.example"] },
  ]);

  // remove a sender and a whole source
  await app.getByLabel("Remove alerts@jobs.example").click();
  await app.getByRole("button", { name: "Remove source Placement cell" }).click();
  await expect(cards).toHaveCount(3);
  await save.click();
  await expect(saved.last()).toBeVisible();
  await app.reload();
  await expect(cards).toHaveCount(3);
  await expect(app.getByRole("list", { name: "Internship senders entries" })).toHaveCount(0);
  await expect(app.getByRole("textbox", { name: "Placement cell senders" })).toHaveCount(0);
  const settings = await api<{ sources: { name: string }[]; digest_hour: number }>(app, "/settings");
  expect(settings.sources.map((s) => s.name)).toEqual(["NPTEL", "Digii", "Internship"]);
  expect(settings.digest_hour).toBe(20);
});

test("watch keywords, block senders and the deadline toggle: add, reject bad entries, remove, persist after reload", async ({ app }) => {
  await app.goto("/settings");
  const keywords = app.getByTestId("keywords");
  const block = app.getByTestId("block-senders");
  const save = app.getByRole("button", { name: "Save settings" });
  const saved = app.getByRole("status").filter({ hasText: "Settings saved" });
  const toggle = app.getByLabel("Only create tasks that have a deadline");
  await expect(keywords).toBeVisible(); // settings have loaded

  await expect(keywords.getByRole("list", { name: "Watch keywords entries" })).toContainText("hackathon");
  await expect(keywords.getByRole("list", { name: "Watch keywords entries" })).toContainText("ideathon");
  await expect(keywords.getByRole("list", { name: "Watch keywords entries" })).toContainText("datathon");
  await expect(toggle).not.toBeChecked();

  await keywords.getByRole("textbox").fill("in:inbox");
  await keywords.getByRole("button", { name: "Add" }).click();
  await expect(keywords.locator('p[role="alert"]')).toContainText("letters, numbers");
  await keywords.getByRole("textbox").fill("  Smart   INDIA ");
  await keywords.getByRole("textbox").press("Enter");
  await expect(keywords.getByRole("list", { name: "Watch keywords entries" })).toContainText("smart india");
  await app.getByLabel("Remove datathon").click();

  await block.getByRole("textbox").fill("not a domain");
  await block.getByRole("button", { name: "Add" }).click();
  await expect(block.locator('p[role="alert"]')).toContainText("domain");
  await block.getByRole("textbox").fill("@Promo.Example");
  await block.getByRole("textbox").press("Enter");
  await expect(block.getByRole("list", { name: "Block senders entries" })).toContainText("promo.example");

  await toggle.check();
  await save.click();
  await expect(saved).toBeVisible();

  await app.reload();
  await expect(app.getByTestId("keywords").getByRole("list", { name: "Watch keywords entries" })).toContainText("smart india");
  await expect(app.getByTestId("keywords").getByRole("list", { name: "Watch keywords entries" })).not.toContainText("datathon");
  await expect(app.getByTestId("block-senders").getByRole("list", { name: "Block senders entries" })).toContainText("promo.example");
  await expect(app.getByLabel("Only create tasks that have a deadline")).toBeChecked();
  expect(await api(app, "/settings")).toMatchObject({
    watch_keywords: ["hackathon", "ideathon", "smart india"],
    block_senders: ["promo.example"],
    require_deadline: true,
  });

  // and switching it back off persists too
  await app.getByLabel("Only create tasks that have a deadline").uncheck();
  await app.getByLabel("Remove promo.example").click();
  await app.getByRole("button", { name: "Save settings" }).click();
  await expect(saved.last()).toBeVisible();
  await app.reload();
  await expect(app.getByLabel("Only create tasks that have a deadline")).not.toBeChecked();
  await expect(app.getByTestId("block-senders").getByRole("list", { name: "Block senders entries" })).toHaveCount(0);
  expect(await api(app, "/settings")).toMatchObject({ block_senders: [], require_deadline: false });
});

test('tasks with no deadline show a "No deadline" badge, and the badge carries the source name', async ({ app }) => {
  await app.goto("/email");
  await waitForTasks(app, "From Email");
  const digii = card(app, "Digii quiz without a date");
  await expect(digii.getByTestId("no-deadline")).toHaveText("No deadline");
  await expect(digii.getByTestId("source-badge")).toHaveText("Digii");
  await expect(app.locator("section", { has: app.getByRole("heading", { name: /^Important/ }) })).toContainText("Digii quiz without a date");
  await app.goto("/");
  await waitForTasks(app);
  await expect(card(app, "Edit me").getByTestId("no-deadline")).toBeVisible();
  await app.goto("/email");
  await waitForTasks(app, "From Email");
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

  for (const [path, name] of [["/", "tasks"], ["/email", "email"], ["/deadlines", "deadlines"], ["/attention", "attention"], ["/settings", "settings"]]) {
    await page.goto(path);
    await expect(page.getByRole("status", { name: /Loading/ })).toHaveCount(0);
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `e2e/screenshots/mobile-380-${name}.png` });
  }

  await page.goto("/email");
  await waitForTasks(page, "From Email");
  const nav = page.getByRole("navigation", { name: "Main" }).last();
  await expect(nav).toBeVisible();
  for (const label of ["My Tasks", "From Email", "Deadlines"]) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  await nav.getByRole("link", { name: "Deadlines" }).click();
  await expect(page).toHaveURL(/\/deadlines$/);
  await expect(page.getByRole("heading", { name: "Deadlines", level: 1 })).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/mobile-380-bottom-nav.png" });
  await nav.getByRole("link", { name: "My Tasks" }).click();
  await expect(page).toHaveURL(/localhost:3100\/$/);
  await expect(nav.getByRole("link", { name: "My Tasks" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
  await expect(page.getByTestId("attention-link")).toBeVisible();
  await nav.getByRole("link", { name: "From Email" }).click();
  await waitForTasks(page, "From Email");
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

// ------------------------------------------------------------------ subtasks

test("subtasks: create a task with 3 subtasks, tick them, watch the progress bar, get asked before completing", async ({ app }) => {
  await app.goto("/");
  await waitForTasks(app);
  await app.getByRole("button", { name: "New task" }).click();
  const dialog = app.getByRole("dialog", { name: "New task", exact: true });
  await dialog.getByLabel("Title").fill("Write the report");
  await dialog.getByLabel("Deadline (India time)").fill(istInput(3));
  await dialog.getByLabel("Tag").fill("Uni");
  for (const sub of ["Outline", "Draft", "Proofread"]) {
    await dialog.getByLabel("New subtask").fill(sub);
    await dialog.getByRole("button", { name: "Add subtask" }).click();
  }
  await expect(dialog.getByTestId("subtask-row")).toHaveCount(3);
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toHaveCount(0);

  const task = card(app, "Write the report");
  await expect(task.getByTestId("task-tag")).toHaveText("#Uni");
  await expect(task.getByTestId("priority-badge")).toHaveText("Regular");
  await expect(task).toContainText(/Due in 2d/);
  await expect(task.getByTestId("subtask-count")).toHaveText("0/3");
  await expect(task.getByRole("progressbar", { name: "Subtasks done" })).toHaveAttribute("aria-valuenow", "0");

  await task.getByRole("button", { name: "Show checklist" }).click();
  const checks = task.getByRole("checkbox");
  await expect(checks).toHaveCount(3);
  await checks.nth(0).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("1/3");
  await expect(task.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
  await checks.nth(1).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("2/3");
  const prompt = app.getByRole("dialog", { name: "Mark the whole task done?" });
  await expect(prompt).toHaveCount(0); // not yet

  await checks.nth(2).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("3/3");
  await expect(prompt).toBeVisible();
  // saying no leaves the task open
  await prompt.getByRole("button", { name: "Cancel" }).click();
  await expect(task).toBeVisible();
  const stored = (await api<ApiTask[]>(app, "/tasks?origin=manual&limit=500")).find((t) => t.title === "Write the report")!;
  expect(stored.status).toBe("open");

  // untick and tick again: asked again; yes completes it
  await checks.nth(2).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("2/3");
  await checks.nth(2).click();
  await app.getByRole("dialog", { name: "Mark the whole task done?" }).getByRole("button", { name: "Mark task done" }).click();
  await expect(card(app, "Write the report")).toHaveCount(0);
  const after = (await api<ApiTask[]>(app, "/tasks?origin=manual&status=done&limit=500")).find((t) => t.title === "Write the report");
  expect(after?.status).toBe("done");
});

test("subtasks: the form edits them (rename, delete, reorder by drag) and keeps reminders off and a tag", async ({ app }) => {
  const res = await app.request.post("/api/tasks", {
    data: { title: "Form edit task", tag: "Home", subtasks: ["one", "two", "three"], reminders_enabled: false },
  });
  expect(res.ok()).toBeTruthy();
  await app.goto("/");
  await waitForTasks(app);
  await card(app, "Form edit task").getByRole("button", { name: "Edit Form edit task" }).click();
  const edit = app.getByRole("dialog", { name: "Edit task" });
  await expect(edit.getByLabel("Remind me about this task")).not.toBeChecked();
  await expect(edit.getByLabel("Tag")).toHaveValue("Home");
  await expect(edit.getByTestId("subtask-row")).toHaveCount(3);

  await edit.getByRole("textbox", { name: "Subtask 2", exact: true }).fill("two renamed");
  await edit.getByRole("button", { name: "Delete subtask 3" }).click();
  await edit.getByLabel("New subtask").fill("four");
  await edit.getByRole("button", { name: "Add subtask" }).click();
  // drag the last row to the top
  await edit.getByTestId("subtask-row").nth(2).dragTo(edit.getByTestId("subtask-row").nth(0));
  await expect(edit.getByRole("textbox", { name: "Subtask 1", exact: true })).toHaveValue("four");
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(edit).toHaveCount(0);

  const stored = (await api<(ApiTask & { subtasks: { title: string }[]; reminders_enabled: boolean; tag: string })[]>(app, "/tasks?origin=manual&limit=500")).find((t) => t.title === "Form edit task")!;
  expect(stored.subtasks.map((s) => s.title)).toEqual(["four", "one", "two renamed"]);
  expect(stored).toMatchObject({ reminders_enabled: false, tag: "Home" });
  await expect(card(app, "Form edit task").getByTestId("subtask-count")).toHaveText("0/3");

  // the new-task form suggests tags already in use
  await app.getByRole("button", { name: "New task" }).click();
  const create = app.getByRole("dialog", { name: "New task", exact: true });
  await expect(create.locator("datalist option[value='Home']")).toHaveCount(1);
  await expect(create.locator("datalist option[value='Uni']")).toHaveCount(1);
  await app.keyboard.press("Escape");
});

test("subtasks: add one to an email task, and it counts", async ({ app }) => {
  await app.goto("/email");
  await waitForTasks(app, "From Email");
  const task = card(app, "Submit NPTEL Assignment 5");
  await expect(task.getByTestId("subtask-count")).toHaveCount(0);
  await expect(task.getByRole("button", { name: /Dismiss/ })).toBeVisible(); // email tasks are dismissed, not deleted
  await expect(task.getByRole("button", { name: /^Delete/ })).toHaveCount(0);
  await task.getByRole("button", { name: "Add a checklist" }).click();
  await task.getByLabel("Add a subtask to Submit NPTEL Assignment 5").fill("Revise week 5 notes");
  await task.getByRole("button", { name: "Add", exact: true }).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("0/1");
  await task.getByRole("checkbox", { name: "Revise week 5 notes" }).click();
  await expect(task.getByTestId("subtask-count")).toHaveText("1/1");
  await app.getByRole("dialog", { name: "Mark the whole task done?" }).getByRole("button", { name: "Cancel" }).click();

  const stored = (await api<{ title: string; subtasks_total: number; subtasks_done: number; status: string }[]>(app, "/tasks?origin=email&limit=500")).find((t) => t.title === "Submit NPTEL Assignment 5")!;
  expect(stored).toMatchObject({ subtasks_total: 1, subtasks_done: 1, status: "open" });
  await app.reload();
  await waitForTasks(app, "From Email");
  await expect(card(app, "Submit NPTEL Assignment 5").getByTestId("subtask-count")).toHaveText("1/1");

  // email tasks edit title and deadline only
  await card(app, "Submit NPTEL Assignment 5").getByRole("button", { name: /^Edit/ }).click();
  const edit = app.getByRole("dialog", { name: "Edit email task" });
  await expect(edit.getByLabel("Tag")).toHaveCount(0);
  await expect(edit.getByLabel("Deadline (India time)")).toBeVisible();
  await app.keyboard.press("Escape");
});

// ------------------------------------------------------------------ deadlines

test("deadlines: groups, mixed deadline order, filters, quick done and show completed", async ({ app }) => {
  const at = (days: number, hourIst: number) => {
    const d = new Date(Date.now() + days * 86_400_000 + 5.5 * 3_600_000);
    d.setUTCHours(hourIst, 0, 0, 0);
    return new Date(d.getTime() - 5.5 * 3_600_000).toISOString();
  };
  const create = async (title: string, deadline: string | null) => {
    const res = await app.request.post("/api/tasks", { data: { title, ...(deadline ? { deadline } : {}) } });
    expect(res.ok()).toBeTruthy();
  };
  await create("DL overdue manual", new Date(Date.now() - 2 * 86_400_000).toISOString());
  await create("DL today manual", new Date(Date.now() + 120_000).toISOString());
  await create("DL tomorrow manual", at(1, 12));
  await create("DL week manual", at(3, 12));
  await create("DL later manual", at(20, 12));
  await create("DL none manual", null);

  await app.goto("/deadlines");
  await expect(app.getByRole("heading", { name: "Deadlines", level: 1 })).toBeVisible();
  await expect(app.getByTestId("deadline-row").first()).toBeVisible();

  const group = (key: string) => app.getByTestId(`group-${key}`);
  await expect(group("overdue").getByRole("heading", { name: /Overdue/ })).toHaveClass(/text-danger/);
  await expect(group("overdue")).toContainText("DL overdue manual");
  await expect(group("overdue")).toContainText("Overdue by");
  await expect(group("today")).toContainText("DL today manual");
  await expect(group("tomorrow")).toContainText("DL tomorrow manual");
  await expect(group("week")).toContainText("DL week manual");
  await expect(group("later")).toContainText("DL later manual");
  await expect(group("none")).toContainText("DL none manual");
  await expect(group("none")).toContainText("Digii quiz without a date");

  // manual and email tasks are mixed, in the same order the API gives
  const dom = await app.getByTestId("deadline-row").evaluateAll((rows) =>
    rows.map((r) => ({ title: r.querySelector("p")?.textContent ?? "", origin: r.getAttribute("data-origin") })),
  );
  const apiOrder = (await api<ApiTask[]>(app, "/deadlines")).map((t) => t.title);
  expect(dom.map((r) => r.title)).toEqual(apiOrder);
  expect(new Set(dom.map((r) => r.origin))).toEqual(new Set(["manual", "email"]));
  const titles = dom.map((r) => r.title);
  expect(titles.indexOf("DL week manual")).toBeLessThan(titles.indexOf("Fill the hackathon registration form")); // +3d before +5d
  expect(titles.indexOf("Fill the hackathon registration form")).toBeLessThan(titles.indexOf("Read the exam circular")); // +5d before +10d
  expect(titles.indexOf("Read the exam circular")).toBeLessThan(titles.indexOf("DL later manual")); // +10d before +20d
  // each row shows origin, tag/source, IST deadline
  const email = app.getByTestId("deadline-row").filter({ hasText: "Fill the hackathon registration form" });
  await expect(email).toHaveAttribute("data-origin", "email");
  await expect(email).toContainText("IST");
  await expect(email.getByRole("link", { name: /Open Fill the hackathon/ })).toHaveAttribute("href", "https://forms.gle/Hackathon26");
  const nptel = app.getByTestId("deadline-row").filter({ hasText: "Submit NPTEL Assignment 5" });
  await expect(nptel).toContainText("NPTEL");
  await expect(nptel.getByTestId("subtask-progress")).toContainText("1/1");

  // filters
  await app.getByRole("button", { name: "My Tasks", exact: true }).click();
  await expect(app.getByTestId("deadline-row").filter({ hasText: "Fill the hackathon" })).toHaveCount(0);
  await expect(app.getByTestId("deadline-row").filter({ hasText: "DL week manual" })).toHaveCount(1);
  for (const row of await app.getByTestId("deadline-row").all()) await expect(row).toHaveAttribute("data-origin", "manual");
  await app.getByRole("button", { name: "From Email", exact: true }).click();
  await expect(app.getByTestId("deadline-row").filter({ hasText: "DL week manual" })).toHaveCount(0);
  for (const row of await app.getByTestId("deadline-row").all()) await expect(row).toHaveAttribute("data-origin", "email");
  await app.getByRole("button", { name: "All", exact: true }).click();

  // quick done, and the completed toggle
  await expect(app.getByText("Completed", { exact: true })).toHaveCount(0);
  await app.getByRole("checkbox", { name: "Mark DL later manual done" }).click();
  await expect(app.getByTestId("deadline-row").filter({ hasText: "DL later manual" })).toHaveCount(0);
  await app.getByLabel("Show completed").check();
  const completed = group("completed");
  await expect(completed).toContainText("DL later manual");
  await completed.getByRole("checkbox", { name: "Reopen DL later manual" }).click();
  await expect(group("later")).toContainText("DL later manual");
  await app.getByLabel("Show completed").uncheck();
});

// ------------------------------------------------------------------ the shield

test("shield icon: shown only while Suspicious or Needs review have items, and opens both lists", async ({ app }) => {
  await app.goto("/email");
  await waitForTasks(app, "From Email");
  const shield = app.getByTestId("attention-link");
  await expect(shield).toBeVisible();
  const items = (await api<unknown[]>(app, "/review")).length + (await api<unknown[]>(app, "/quarantine")).length;
  expect(items).toBeGreaterThan(0);
  await expect(badge(app, "attention")).toHaveText(String(items));
  await expect(app.getByRole("navigation", { name: "Main" }).first().getByRole("link", { name: /Suspicious|Review/ })).toHaveCount(0);

  await shield.click();
  await expect(app).toHaveURL(/\/attention$/);
  await expect(app.getByRole("heading", { name: "Needs your attention", level: 1 })).toBeVisible();
  await expect(app.getByRole("heading", { name: "Suspicious", level: 2 })).toBeVisible();
  await expect(app.getByRole("heading", { name: "Needs review", level: 2 })).toBeVisible();

  // clear both lists: the icon goes away
  for (const t of await api<{ id: number }[]>(app, "/review")) {
    expect((await app.request.post(`/api/tasks/${t.id}/dismiss`)).ok()).toBeTruthy();
  }
  for (const q of await api<{ id: number }[]>(app, "/quarantine")) {
    expect((await app.request.post(`/api/quarantine/${q.id}/confirm-scam`)).ok()).toBeTruthy();
  }
  await app.goto("/");
  await waitForTasks(app);
  await expect(app.getByTestId("attention-link")).toHaveCount(0);
  await expect(app.getByRole("link", { name: "Settings" })).toBeVisible(); // the gear stays
});

// ------------------------------------------------------------------ rebuild from sources

test("rebuild from sources: asks first, reports counts, keeps the user's own tasks, and is repeatable", async ({ app }) => {
  // a task of the user's own, which no rebuild may remove
  expect((await app.request.post("/api/tasks", { data: { title: "Rebuild must keep me" } })).ok()).toBeTruthy();
  await app.goto("/settings");
  const button = app.getByRole("button", { name: "Rebuild from sources" });
  await expect(button).toBeEnabled();

  // the confirmation says what will happen, and cancelling changes nothing
  await button.click();
  const dialog = app.getByRole("dialog", { name: "Rebuild from sources?" });
  await expect(dialog.getByTestId("rebuild-preview")).toContainText("will not be touched");
  await expect(dialog.getByTestId("rebuild-preview")).toContainText("no longer in any source");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  expect((await api<ApiTask[]>(app, "/tasks?status=open&limit=500")).some((t) => t.title === "New Quiz Published: Week 9 Quiz")).toBe(false);

  // unsaved edits must be saved first
  await app.getByLabel("Only create tasks that have a deadline").check();
  await expect(button).toBeDisabled();
  await app.getByLabel("Only create tasks that have a deadline").uncheck();
  await expect(button).toBeEnabled();

  await button.click();
  await app.getByTestId("rebuild-preview").getByText("tasks (added by you").waitFor();
  await app.getByRole("dialog", { name: "Rebuild from sources?" }).getByRole("button", { name: "Rebuild now" }).click();
  const done = app.getByRole("dialog", { name: "Rebuild finished" });
  await expect(done.getByTestId("rebuild-summary")).toContainText(/Removed \d+, added \d+, kept \d+\./);
  await expect(done.getByTestId("rebuild-summary")).toContainText("Open tasks by source:");
  await expect(done.getByTestId("rebuild-summary")).toContainText("suspicious");
  await done.getByRole("button", { name: "Close" }).last().click();

  // mail from the sources became tasks (a source mail is a task even when its date has passed)
  const open = await api<(ApiTask & { source: string; source_name: string | null })[]>(app, "/tasks?status=open&limit=500");
  const digii = open.find((t) => t.title === "New Quiz Published: Week 9 Quiz");
  expect(digii).toMatchObject({ source: "digii", source_name: "Digii" });
  expect(open.some((t) => t.title.toLowerCase().includes("pay the registration fee"))).toBe(false); // the scam stays out
  // the user's own tasks are untouched
  const titles = (await api<ApiTask[]>(app, "/tasks?limit=500")).map((t) => t.title);
  expect(titles).toContain("Rebuild must keep me");
  // an automatic task whose mail is not in the sources any more is gone
  expect(titles).not.toContain("Read the exam circular");

  // doing it again changes nothing
  const before = (await api<ApiTask[]>(app, "/tasks?limit=500")).map((t) => t.id).sort();
  await app.getByRole("button", { name: "Rebuild from sources" }).click();
  await app.getByTestId("rebuild-preview").getByText("tasks (added by you").waitFor();
  await app.getByRole("dialog", { name: "Rebuild from sources?" }).getByRole("button", { name: "Rebuild now" }).click();
  await expect(app.getByRole("dialog", { name: "Rebuild finished" }).getByTestId("rebuild-summary")).toContainText(
    /Removed 0, added 0, kept \d+\./,
  );
  await app.getByRole("dialog", { name: "Rebuild finished" }).getByRole("button", { name: "Close" }).last().click();
  expect((await api<ApiTask[]>(app, "/tasks?limit=500")).map((t) => t.id).sort()).toEqual(before);

  await app.getByRole("link", { name: "From Email" }).first().click();
  await waitForTasks(app, "From Email");
  await expect(card(app, "New Quiz Published: Week 9 Quiz").getByTestId("source-badge")).toHaveText("Digii");
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
