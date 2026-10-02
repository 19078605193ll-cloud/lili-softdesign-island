import { test, expect } from "@playwright/test";

test("registration, both login identifiers, learning statistics and admin account status", async ({ page, browser }, info) => {
  const username = "registered-" + info.project.name;
  const email = username + "@example.com";
  const password = "registration-password-123";
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto("/h5/");
  await expect(page).toHaveURL(/\/h5\/login/);
  await page.getByRole("link", { name: "立即注册" }).click();
  await expect(page).toHaveURL(/\/h5\/register/);
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码", { exact: true }).fill("different-password-123");
  await page.getByRole("button", { name: "注册并开始学习" }).click();
  await expect(page.getByRole("alert")).toHaveText("两次输入的密码不一致");
  await page.getByLabel("确认密码", { exact: true }).fill(password);
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({ path: info.outputPath("register.png"), fullPage: true });
  await page.getByRole("button", { name: "注册并开始学习" }).click();
  await expect(page.getByRole("heading", { name: "学情看板" })).toBeVisible();
  await page.goto("/h5/login");
  await expect(page).toHaveURL(/\/h5\/$/);

  async function logout() {
    await page.getByRole("button", { name: "个人账号" }).click();
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(page.getByLabel("用户名或邮箱", { exact: true })).toBeVisible();
  }
  async function login(identifier: string) {
    await page.getByLabel("用户名或邮箱", { exact: true }).fill(identifier);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录，继续学习" }).click();
  }
  for (const identifier of [email.toUpperCase(), username]) {
    await logout();
    await login(identifier);
    await expect(page.getByRole("heading", { name: "学情看板" })).toBeVisible();
  }
  // Duplicate registration must retain the supplied identifiers and show the conflict.
  await logout();
  await page.getByRole("link", { name: "立即注册" }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page.getByLabel("用户名", { exact: true }).fill(username + "-duplicate");
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByLabel("确认密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "注册并开始学习" }).click();
  await expect(page.getByRole("alert")).toContainText("邮箱已被使用");
  await expect(page.getByLabel("邮箱", { exact: true })).toHaveValue(email);
  await page.getByRole("link", { name: "返回登录" }).click();
  await login(email);
  await expect(page.getByRole("heading", { name: "学情看板" })).toBeVisible();

  await page.getByRole("link", { name: "刷题", exact: true }).click();
  await page.getByRole("button", { name: /回忆版测试/ }).click();
  await expect(page.locator(".question-part")).toHaveCount(2);
  const sessionUrl = page.url();
  await page.locator(".question-part").nth(0).locator(".option").nth(0).click();
  await page.locator(".question-part").nth(1).locator(".option").nth(2).click();
  await page.getByRole("button", { name: "提交整题答案" }).click();
  await expect(page.getByText("本题答错了，再梳理一下思路")).toBeVisible();
  await expect(page.getByText("隔离测试：先判断这一步的输入与输出分别是什么？")).toBeVisible();

  const adminContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  try {
    const admin = await adminContext.newPage();
    admin.on("pageerror", e => errors.push(e.message));
    await admin.goto("/admin/users");
    await admin.locator("[name=username]").fill("users-admin-" + info.project.name);
    await admin.locator("[name=password]").fill("isolated-test-password");
    await admin.getByRole("button", { name: "登录", exact: true }).click();
    await admin.getByRole("link", { name: "用户管理", exact: true }).click();
    await admin.getByLabel("用户名或邮箱", { exact: true }).fill(email);
    await admin.getByRole("button", { name: "搜索", exact: true }).click();
    const row = admin.locator("#users tr").filter({ hasText: username });
    await expect(row).toHaveCount(1);
    await expect(row.locator("td").nth(7)).toHaveText("1");
    await expect(row.locator("td").nth(8)).toHaveText("50%");
    await expect(row.locator("td").nth(5)).not.toHaveText("未记录");
    await admin.screenshot({ path: info.outputPath("users.png"), fullPage: true });
    admin.once("dialog", dialog => dialog.accept());
    await row.getByRole("button", { name: "停用", exact: true }).click();
    await expect(row.getByRole("button", { name: "启用", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("用户名或邮箱", { exact: true })).toBeVisible();
    await login(email);
    await expect(page.getByRole("alert")).toContainText("账号或密码错误");
    await row.getByRole("button", { name: "启用", exact: true }).click();
    await expect(row.getByRole("button", { name: "停用", exact: true })).toBeVisible();
    await login(username);
    await expect(page).toHaveURL(sessionUrl);
    await expect(page.getByText("本题答错了，再梳理一下思路")).toBeVisible();
    await page.reload();
    await expect(page.getByText("本题答错了，再梳理一下思路")).toBeVisible();
    await admin.getByLabel("账号状态").selectOption("false");
    await admin.getByRole("button", { name: "搜索", exact: true }).click();
    await expect(admin.getByText("暂无匹配用户")).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await adminContext.close();
  }
});
