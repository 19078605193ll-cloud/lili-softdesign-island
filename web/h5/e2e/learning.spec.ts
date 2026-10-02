import { test, expect } from "@playwright/test";
test("real API learning loop, restoration, notebook, tutor and mobile layouts", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  // localhost normally exposes randomUUID even over HTTP; emulate public HTTP.
  await page.addInitScript(() => {
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
  });
  await page.goto("/h5/");
  await page
    .getByLabel("用户名或邮箱", { exact: true })
    .fill("h5-" + info.project.name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(page.getByRole("heading", { name: "学情看板" })).toBeVisible();
  for (const width of [320, 375, 390, 414, 430, 768]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    const nav = page.getByRole("navigation", { name: "主导航" });
    await expect(nav).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    const bounds = await nav.boundingBox();
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(901);
    if (width === 390)
      await page.screenshot({
        path: info.outputPath("home.png"),
        fullPage: true,
      });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "编辑考期" }).click();
  await page.getByLabel("考试名称").fill("我的软考目标");
  await page.getByLabel("开始备考").fill("2026-01-01");
  await page.getByLabel("考试日期").fill("2026-11-07");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("我的软考目标")).toBeVisible();
  await page.getByRole("link", { name: "刷题", exact: true }).click();
  await expect(page.getByRole("heading", { name: "历年真题" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
  await page.screenshot({
    path: info.outputPath("practice.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /回忆版测试/ }).click();
  await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
  await expect(page.locator(".question-part")).toHaveCount(2);
  const originalUrl = page.url();
  await page
    .locator(".question-part")
    .nth(0)
    .getByRole("button")
    .filter({ has: page.locator(".option-key", { hasText: "A" }) })
    .click();
  await page.reload();
  await expect(page.locator(".option.chosen .option-key")).toHaveText("A");
  await page
    .locator(".question-part")
    .nth(1)
    .getByRole("button")
    .filter({ has: page.locator(".option-key", { hasText: "C" }) })
    .click();
  await page.getByRole("button", { name: "提交整题答案" }).click();
  await expect(page.getByText("本题答错了，再梳理一下思路")).toBeVisible();
  await expect(
    page.getByText("隔离测试：先判断这一步的输入与输出分别是什么？"),
  ).toBeVisible();
  await page
    .locator(".session-content")
    .evaluate((el) => (el.scrollTop = el.scrollHeight));
  await page.screenshot({
    path: info.outputPath("incorrect.png"),
    fullPage: true,
  });
  await page.getByLabel("收藏题目", { exact: true }).click();
  const composer = page.getByLabel("向AI提问");
  await expect(composer).toHaveJSProperty("tagName", "TEXTAREA");
  await composer.fill("这一段内容比较长，需要输入多行文字。".repeat(25));
  await expect
    .poll(async () => (await composer.boundingBox())!.height)
    .toBeLessThanOrEqual(136);
  const dock = page.locator(".session-dock");
  for (const width of [320, 375, 390, 414, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const box = await dock.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(845);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollHeight <= innerHeight,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await composer.fill("请画出步骤");
  await composer.press("Enter");
  await composer.pressSequentially("谢谢");
  await expect(composer).toHaveValue("请画出步骤\n谢谢");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".diagram svg")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "做一道变式题，检验理解" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "再练一道类似题" }).click();
  // The unattempted real question takes priority over generation.
  await expect(page.locator(".question-part")).toHaveCount(1);
  await page.locator(".question-part .option").nth(2).click();
  await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "返回列表", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(originalUrl);
  await expect(page.locator(".diagram svg")).toBeVisible();
  await page.getByRole("button", { name: "再练一道类似题" }).click();
  await expect(page.getByText("AI生成教学题 · 不计正式成绩")).toBeVisible();
  await page.locator(".variant-card .option").first().click();
  await expect(
    page.locator(".variant-card").getByText(/正确答案 A/),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.locator(".variant-card").getByText(/正确答案 A/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "返回原题", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(originalUrl);
  await page
    .getByRole("button", { name: "返回列表", exact: true })
    .first()
    .click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "首页", exact: true })
    .click();
  await page.getByRole("link", { name: "错题本", exact: true }).click();
  await expect(page.locator(".notebook-card")).toHaveCount(1);
  await page.screenshot({
    path: info.outputPath("notebook.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "再做一次", exact: true }).click();
  await page.locator(".question-part").nth(0).locator(".option").nth(0).click();
  await page.locator(".question-part").nth(1).locator(".option").nth(1).click();
  await page.getByRole("button", { name: "提交整题答案" }).click();
  await expect(
    page.getByText("已解除本题的错题和犹豫状态，收藏保持不变。"),
  ).toBeVisible();
  await page.locator(".hesitant-link").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("correct.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /本题有疑问，问问 AI/ }).click();
  await page
    .getByRole("button", { name: "返回列表", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "犹豫题", exact: true }).click();
  await expect(page.locator(".notebook-card")).toHaveCount(1);
  await page.getByRole("button", { name: "解除", exact: true }).click();
  await expect(page.locator(".notebook-card")).toHaveCount(0);
  await page.getByRole("button", { name: "收藏题", exact: true }).click();
  await expect(page.locator(".notebook-card")).toHaveCount(1);
  await page.getByRole("button", { name: "取消收藏", exact: true }).click();
  await expect(page.locator(".notebook-card")).toHaveCount(0);
  await page.getByRole("link", { name: "返回首页" }).click();
  await page.getByRole("link", { name: "刷题", exact: true }).click();
  await page.getByRole("button", { name: /回忆版测试/ }).click();
  await page.getByRole("button", { name: "下一题", exact: true }).click();
  await expect(page.locator(".question-part")).toHaveCount(1);
  await expect(page.locator(".question-part .option").nth(2)).toBeDisabled();
  await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
  await page.locator(".hesitant-link").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: info.outputPath("single-correct.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "返回列表", exact: true })
    .first()
    .click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("link", { name: "首页", exact: true })
    .click();
  await page.getByRole("button", { name: "个人账号" }).click();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByLabel("用户名或邮箱", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
