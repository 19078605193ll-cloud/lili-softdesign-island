import { test, expect, type Page } from "@playwright/test";

async function mockPractice(
  page: Page,
  options: {
    composite?: boolean;
    marksFail?: boolean;
    detailRetry?: boolean;
  } = {},
) {
  let position = 0;
  const attempts: Record<string, string> = {};
  const results: Record<string, any> = {};
  let failed = false;
  const question = (id: string) => ({
    id,
    content_version: 1,
    type: options.composite ? "composite" : "single",
    source: { year: 2026, source_type: "recalled" },
    parts: Array.from({ length: options.composite ? 2 : 1 }, (_, i) => ({
      id: id + "-" + i,
      question_no: i + 1,
      stem_html: "<p>测试题目</p>",
      options: [
        { key: "A", content_html: "<p>正确选项</p>" },
        { key: "B", content_html: "<p>错误选项</p>" },
      ],
      correct_option_keys: ["A"],
      explanation_html: "",
    })),
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON();
    let data: any = {};
    if (path.endsWith("/auth/me")) data = { id: "streak-user" };
    else if (path.endsWith("/auth/csrf")) data = { csrf_token: "test" };
    else if (path.endsWith("/subjects")) data = { items: [{ id: "subject" }] };
    else if (path.includes("/learning/sessions/")) {
      if (body?.position !== undefined) position = body.position;
      data = {
        id: "streak-session",
        title: "测试练习",
        position,
        question_ids: Array.from({ length: 12 }, (_, i) => "q" + i),
        attempts,
        drafts: {},
        read_only: false,
      };
    } else if (path.endsWith("/marks")) {
      if (options.marksFail && Object.keys(results).length)
        return route.fulfill({
          status: 500,
          json: { message: "标记读取失败" },
        });
      data = { items: [] };
    } else if (path.endsWith("/learning/attempts")) {
      const id = "a-" + body.practice_question_id;
      const snapshot = question(body.practice_question_id);
      attempts[snapshot.id] = id;
      results[id] = {
        id,
        snapshot,
        parts: snapshot.parts.map((p) => ({
          question_id: p.id,
          answer: body.answers[p.id],
          correct: body.answers[p.id] === "A",
        })),
      };
      data = { id };
    } else if (path.includes("/learning/attempts/")) {
      if (options.detailRetry && !failed) {
        failed = true;
        return route.fulfill({ status: 500, json: { message: "读取失败" } });
      }
      data = results[path.split("/").pop()!];
    } else if (path.includes("/practice/questions/"))
      data = question(path.split("/").pop()!);
    await route.fulfill({ json: data });
  });
  await page.goto("http://127.0.0.1:5179/h5/session/streak-session");
}
async function answer(page: Page) {
  const parts = page.locator(".question-part");
  for (let i = 0; i < (await parts.count()); i++)
    await parts.nth(i).locator(".option").first().click();
  const submit = page.getByRole("button", { name: "提交整题答案" });
  if (await submit.count()) await submit.click();
}
test("milestones, restoration, overlay interaction and cleanup", async ({
  page,
}) => {
  await mockPractice(page);
  for (let n = 1; n <= 11; n++) {
    await expect(page.locator(".question-part")).toHaveCount(1);
    const before = await page.locator(".question-card").boundingBox();
    await answer(page);
    await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
    if ([3, 5, 10].includes(n)) {
      await expect(page.getByRole("status")).toHaveText(`连对 ${n} 题`);
      await expect(page.locator(".streak-celebration canvas")).toBeVisible();
      expect(
        await page
          .locator(".streak-celebration")
          .evaluate((el) => getComputedStyle(el).pointerEvents),
      ).toBe("none");
      const after = await page.locator(".question-card").boundingBox();
      expect(after?.y).toBe(before?.y);
      expect(after?.width).toBe(before?.width);
      await page.screenshot({
        path: test.info().outputPath(`streak-${n}.png`),
      });
    } else await expect(page.locator(".streak-celebration")).toHaveCount(0);
    if (n === 2 || n === 5) {
      await page.reload();
      await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
      await expect(page.locator(".streak-celebration")).toHaveCount(0);
    }
    await page.getByRole("button", { name: "下一题" }).click();
    await expect(page.locator(".streak-celebration")).toHaveCount(0);
    await expect(page.locator(".feedback")).toHaveCount(0);
  }
});
test("composite answer counts once, retry does not duplicate, reduced motion keeps notice", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockPractice(page, { composite: true, detailRetry: true });
  for (let n = 1; n <= 3; n++) {
    await expect(page.locator(".question-part")).toHaveCount(2);
    await answer(page);
    if (n === 1) await page.getByRole("button", { name: "重试提交" }).click();
    await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
    if (n < 3) {
      await page.getByRole("button", { name: "下一题" }).click();
      await expect(page.locator(".feedback")).toHaveCount(0);
    }
  }
  await expect(page.getByRole("status")).toHaveText("连对 3 题");
  await expect(page.locator(".streak-celebration canvas")).toBeHidden();
  await expect(page.locator(".streak-celebration")).toHaveCount(0, {
    timeout: 3000,
  });
});
test("marks failure does not discard a confirmed answer", async ({ page }) => {
  await mockPractice(page, { marksFail: true });
  await expect(page.locator(".question-part")).toHaveCount(1);
  await answer(page);
  await expect(page.getByRole("alert")).toContainText("标记读取失败");
  const count = await page.evaluate(
    () =>
      JSON.parse(
        sessionStorage.getItem(
          'answer-streak:v1:["streak-user","streak-session"]',
        )!,
      ).count,
  );
  expect(count).toBe(1);
});

test("a late judgment cannot celebrate after leaving the answer page", async ({
  page,
}) => {
  await mockPractice(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let arrived!: () => void;
  const waiting = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  await page.route("**/api/v2/learning/attempts/a-*", async (route) => {
    arrived();
    await gate;
    await route.fallback();
  });
  await expect(page.locator(".question-part")).toHaveCount(1);
  await answer(page);
  await waiting;
  await page.getByRole("button", { name: "返回列表", exact: true }).click();
  await expect(page).toHaveURL(/\/practice$/);
  const response = page.waitForResponse("**/api/v2/learning/attempts/a-*");
  release();
  await response;
  await expect(page.locator(".streak-celebration")).toHaveCount(0);
  expect(
    await page.evaluate(() =>
      Object.keys(sessionStorage).filter((key) =>
        key.startsWith("answer-streak:"),
      ),
    ),
  ).toEqual([]);
});

test("one incorrect part resets the whole-question streak without a new notice", async ({
  page,
}) => {
  await mockPractice(page, { composite: true });
  await page.evaluate(() =>
    sessionStorage.setItem(
      'answer-streak:v1:["streak-user","streak-session"]',
      JSON.stringify({ count: 2, attempts: ["old-1", "old-2"] }),
    ),
  );
  await expect(page.locator(".question-part")).toHaveCount(2);
  await page
    .locator(".question-part")
    .nth(0)
    .locator(".option")
    .first()
    .click();
  await page.locator(".question-part").nth(1).locator(".option").last().click();
  await page.getByRole("button", { name: "提交整题答案" }).click();
  await expect(page.locator(".feedback.incorrect")).toBeVisible();
  await expect(page.locator(".streak-celebration")).toHaveCount(0);
  expect(
    await page.evaluate(
      () =>
        JSON.parse(
          sessionStorage.getItem(
            'answer-streak:v1:["streak-user","streak-session"]',
          )!,
        ).count,
    ),
  ).toBe(0);
});
