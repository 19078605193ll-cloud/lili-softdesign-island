import { test, expect } from "@playwright/test";
test("profile name and avatar persist, crop cancels and default restores", async ({
  page,
}, info) => {
  const name = "profile-" + info.project.name;
  await page.goto("/h5/");
  await page.getByLabel("用户名或邮箱", { exact: true }).fill(name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await page.getByRole("button", { name: "个人账号" }).click();
  await page.getByRole("button", { name: "修改用户名" }).click();
  await page.getByLabel("用户名", { exact: true }).fill(name + "-new");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByText("用户名：" + name + "-new", { exact: true }),
  ).toBeVisible();
  const data = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 800;
    c.height = 600;
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = "red";
    ctx.fillRect(0, 0, 800, 600);
    ctx.fillStyle = "blue";
    ctx.fillRect(0, 0, 400, 600);
    return c.toDataURL("image/png").split(",")[1];
  });
  await page
    .locator(".profile-editor input[type=file]")
    .setInputFiles({
      name: "avatar.png",
      mimeType: "image/png",
      buffer: Buffer.from(data, "base64"),
    });
  await expect(page.getByLabel("头像裁剪预览")).toBeVisible();
  await page.getByLabel("缩放", { exact: true }).fill("2");
  const box = (await page.getByLabel("头像裁剪预览").boundingBox())!;
  await page.mouse.move(box.x + 120, box.y + 120);
  await page.mouse.down();
  await page.mouse.move(box.x + 150, box.y + 100);
  await page.mouse.up();
  await page.getByRole("button", { name: "保存头像" }).click();
  await expect(page.locator(".avatar-picker img")).toBeVisible();
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.reload();
  await expect(page.locator(".home-heading img")).toBeVisible();
  await page.getByRole("button", { name: "个人账号" }).click();
  await expect(
    page.getByText("用户名：" + name + "-new", { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath("profile.png") });
  await page
    .locator(".profile-editor input[type=file]")
    .setInputFiles({
      name: "avatar.png",
      mimeType: "image/png",
      buffer: Buffer.from(data, "base64"),
    });
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".avatar-picker img")).toBeVisible();
  await page.getByRole("button", { name: "恢复默认头像" }).click();
  await expect(page.locator(".avatar-picker img")).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.getByLabel("用户名或邮箱", { exact: true }).fill(name + "-new");
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(page.getByRole("button", { name: "个人账号" })).toBeVisible();
});
