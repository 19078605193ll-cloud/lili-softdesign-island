import { test, expect } from "@playwright/test";

test("admin renames, deletes and restores a published paper without reimporting", async ({
  page,
}, info) => {
  const errors: string[] = [];
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/admin/imports");
  await page.locator("[name=username]").fill("test-admin");
  await page.locator("[name=password]").fill("isolated-test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const row = page.locator("tr[data-published=true]").first();
  await expect(row).toBeVisible();
  const original = (await row.getAttribute("data-title"))!;
  const id = (await row.getAttribute("data-batch-id"))!;
  const title = "管理员标题验收-" + info.project.name;
  await row.getByRole("button", { name: "编辑标题" }).click();
  await expect(page.getByLabel("标题", { exact: true })).toHaveValue(original);
  await page.getByLabel("标题", { exact: true }).fill(title);
  await page.getByRole("button", { name: "保存标题" }).click();
  await expect(page.locator(`tr[data-batch-id="${id}"]`)).toContainText(title);
  await page.goto("/h5/practice");
  await expect(
    page.getByRole("button", { name: new RegExp(title) }),
  ).toBeVisible();
  await page.goto("/admin/imports");
  page.once("dialog", (d) => {
    expect(d.message()).toContain("已有答题记录保留");
    return d.accept();
  });
  await page
    .locator(`tr[data-batch-id="${id}"]`)
    .getByRole("button", { name: "删除", exact: true })
    .click();
  await expect(page.locator(`tr[data-batch-id="${id}"]`)).toHaveCount(0);
  await page.goto("/h5/practice");
  await expect(page.getByText("暂无已发布题目", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: new RegExp(title) }),
  ).toHaveCount(0);
  await page.goto("/admin/imports?view=deleted");
  await expect(page.locator(`tr[data-batch-id="${id}"]`)).toContainText(title);
  await page.screenshot({
    path: info.outputPath("deleted-papers.png"),
    fullPage: true,
  });
  page.once("dialog", (d) => d.accept());
  await page
    .locator(`tr[data-batch-id="${id}"]`)
    .getByRole("button", { name: "恢复", exact: true })
    .click();
  await expect(page.locator(`tr[data-batch-id="${id}"]`)).toHaveCount(0);
  await page.goto("/h5/practice");
  await expect(
    page.getByRole("button", { name: new RegExp(title) }),
  ).toBeVisible();
  await page.goto("/admin/imports");
  await page
    .locator(`tr[data-batch-id="${id}"]`)
    .getByRole("button", { name: "编辑标题" })
    .click();
  await page.getByLabel("标题", { exact: true }).fill(original);
  await page.getByRole("button", { name: "保存标题" }).click();
  await expect(page.locator(`tr[data-batch-id="${id}"]`)).toContainText(
    original,
  );
  expect(errors).toEqual([]);
});
