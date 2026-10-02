import { test, expect } from "@playwright/test";

// The component fixture imports Vue source and needs Vite, not the API's built SPA.
test.use({ baseURL: "http://127.0.0.1:5179" });
test("AI answer renders math after sanitization and updates existing answers", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/h5/e2e/fixtures/ai-text.html");
  await expect(page.locator(".ai-text")).toBeAttached();
  const answer = String.raw`引导：

- **页内偏移位数**决定页面大小：低 12 位 → 页面大小为 \(2^{12}\) B。
- **页号位数**决定最多页面数：高 20 位 → 页面数为 $2^{20}$。

## 1. 低 12 位决定页面大小

\[
2^{12}=4096\text{ 字节}
\]

\[4096\text{ 字节}=4\text{ KB}\]

所以页面大小是 **4KB**。

## 2. 高 20 位决定页面数量

$$
2^{20}=1,048,576
$$

也就是 **1M 个页面**。`;
  await page.evaluate(
    (text) =>
      window.dispatchEvent(new CustomEvent("ai-text-update", { detail: text })),
    answer,
  );
  const root = page.locator(".ai-text");
  await expect(root.locator(".katex")).toHaveCount(5);
  await expect(root.locator(".katex-display")).toHaveCount(3);
  await expect(root.locator("math msup")).toHaveCount(4);
  await expect(root.locator("strong", { hasText: "4KB" })).toBeVisible();
  expect(
    await root
      .locator(".katex .katex-mathml")
      .first()
      .evaluate((el) => getComputedStyle(el).position),
  ).toBe("absolute");
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({
    path: info.outputPath("ai-math-fixed.png"),
    fullPage: true,
  });

  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("ai-text-update", {
        detail:
          "**更新后的回答**：\\(2^{10}=1024\\)\n\n$\\frac{$\n\n```mermaid\nflowchart LR\nA[地址] --> B[页号]\n```\n\n<script>alert(1)</script>\n\n![图片](https://example.com/x.png)\n\n\\(\\href{javascript:alert(1)}{x}\\)",
      }),
    ),
  );
  await expect(root.locator(".diagram svg")).toBeVisible();
  await expect(root.locator(".katex-error")).toHaveCount(1);
  await expect(root.getByText("更新后的回答", { exact: true })).toBeVisible();
  await expect(root.locator("script, img, iframe, a")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("long display formulas scroll within the answer on small screens", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/h5/e2e/fixtures/ai-text.html");
  await expect(page.locator(".ai-text")).toBeAttached();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("ai-text-update", {
        detail: "\\[" + "2^{12}+".repeat(25) + "1\\]",
      }),
    ),
  );
  const formula = page.locator(".katex-display");
  await expect(formula).toBeVisible();
  expect(
    await formula.evaluate((el) => el.scrollWidth > el.clientWidth),
  ).toBeTruthy();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
