import { test, expect } from "@playwright/test";

test("recall: publishing resets old training and sends an open learner to new knowledge", async ({ page }, info) => {
  await page.goto("/h5/");
  await page.getByLabel("用户名或邮箱", { exact: true }).fill("users-admin-" + info.project.name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(page.getByRole("heading", { name: "主动回忆", exact: true })).toBeVisible();
  const csrf = (await (await page.request.get("/api/v1/auth/csrf")).json()).csrf_token;
  const headers = { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
  const rows = (await (await page.request.get("/api/v2/admin/recalls")).json()).items;
  const base = await (await page.request.get("/api/v2/admin/recalls/versions/" + rows[0].version_id)).json();
  const body = {
    title: "发布同步旧版 " + info.project.name,
    content: { type: "sentence", paragraphs: [[{ text: "事务的性质：" }, { blank_key: "atomicity" }]], note: "" },
    blanks: [{ key: "atomicity", label: "事务性质", answer: "原子性", hints: ["不可分割", "全部成功或全部失败"] }],
    knowledge_node_ids: base.knowledge_node_ids,
  };
  const created = await page.request.post("/api/v2/admin/recalls", {
    headers, data: { ...body, subject_id: base.subject_id, slug: "publish-reset-" + info.project.name },
  });
  expect(created.status()).toBe(201);
  const row = await created.json();
  async function publishVersion(id: string) {
    let revision = 1;
    for (const action of ["approve", "publish"]) {
      const response = await page.request.post(`/api/v2/admin/recalls/versions/${id}/${action}`, {
        headers, data: { revision },
      });
      expect(response.ok()).toBeTruthy();
      revision = (await response.json()).revision;
    }
    return revision;
  }
  const revision = await publishVersion(row.version_id);
  await page.goto("/h5/recalls/" + row.id);
  await page.getByRole("button", { name: "开始 / 继续回忆 →" }).click();
  await expect(page.getByPlaceholder("凭记忆输入答案")).toBeVisible();
  const oldUrl = page.url();
  const oldId = new URL(oldUrl).pathname.split("/").pop();
  await page.getByPlaceholder("凭记忆输入答案").fill("旧答案");
  await page.getByRole("button", { name: "提交本题", exact: true }).click();
  await expect(page.getByRole("heading", { name: "本题未答对，根据提示再试一次" })).toBeVisible();
  const newTitle = "发布同步新版 " + info.project.name;
  const next = await page.request.post(`/api/v2/admin/recalls/${row.id}/versions`, {
    headers: { ...headers, "If-Match": `${row.version_id}:${revision}` },
    data: { ...body, title: newTitle },
  });
  expect(next.status()).toBe(201);
  const version = await next.json();
  await publishVersion(version.version_id);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("heading", { name: newTitle, exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("旧版学习记录已清除");
  expect((await page.request.get("/api/v2/learning/recall-sessions/" + oldId)).status()).toBe(404);
  // Reloading an old bookmarked training URL also recovers to the current content.
  await page.goto(oldUrl);
  await expect(page.getByRole("heading", { name: newTitle, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始 / 继续回忆 →" }).click();
  await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveValue("");
  await expect(page.getByText("已掌握 0 / 1 空")).toBeVisible();
  await expect(page.getByText("本轮错误 0 次 · 历史错误 0 次")).toBeVisible();
  await expect(page.locator('[aria-label^="你的答案："]')).toHaveCount(0);
});

test("recall: semantic retry and persistent answers in all layouts", async ({
  page,
}, info) => {
  test.setTimeout(180000);
  await page.goto("/h5/");
  await page
    .getByLabel("用户名或邮箱", { exact: true })
    .fill("users-admin-" + info.project.name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(
    page.getByRole("heading", { name: "主动回忆", exact: true }),
  ).toBeVisible();
  const csrf = (await (await page.request.get("/api/v1/auth/csrf")).json())
    .csrf_token;
  const headers = { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin };
  const rows = (await (await page.request.get("/api/v2/admin/recalls")).json())
    .items;
  const base = await (
    await page.request.get(
      "/api/v2/admin/recalls/versions/" + rows[0].version_id,
    )
  ).json();
  const contents = [
    {
      type: "sentence",
      note: "",
      paragraphs: [
        [
          { text: "RISC 指令简单，" },
          { blank_key: "pipeline" },
          { text: "流水线技术。" },
        ],
        [
          { text: "典型控制方式为" },
          { blank_key: "control" },
          { text: "控制。" },
        ],
      ],
    },
    {
      type: "table",
      note: "",
      columns: ["比较维度", "RISC"],
      rows: [
        [
          [{ text: "流水线" }],
          [{ text: "指令简单，" }, { blank_key: "pipeline" }],
        ],
        [[{ text: "典型控制方式" }], [{ blank_key: "control" }]],
      ],
    },
    {
      type: "relation",
      note: "",
      nodes: [
        { id: "simple", x: 140, y: 60, label: { text: "指令简单" } },
        { id: "pipeline", x: 470, y: 60, label: { blank_key: "pipeline" } },
      ],
      edges: [
        { source: "simple", target: "pipeline", label: { text: "流水线实现" } },
      ],
      steps: [[{ text: "典型控制方式为" }, { blank_key: "control" }]],
    },
  ];
  for (const content of contents) {
    const created = await page.request.post("/api/v2/admin/recalls", {
      headers,
      data: {
        subject_id: base.subject_id,
        knowledge_node_ids: base.knowledge_node_ids,
        slug: `semantic-${content.type}-${info.project.name}`,
        title: "语义回填验收 " + content.type,
        content,
        blanks: [
          {
            key: "pipeline",
            label: "流水线适用性",
            answer: "更适合",
            hints: ["考虑实现便利性", "保留比较程度"],
          },
          {
            key: "control",
            label: "典型控制方式",
            answer: "硬布线",
            hints: ["考虑硬件逻辑", "与微程序相对"],
          },
        ],
      },
    });
    expect(created.status()).toBe(201);
    const row = await created.json();
    let revision = 1;
    for (const action of ["submit", "approve", "publish"]) {
      const response = await page.request.post(
        `/api/v2/admin/recalls/versions/${row.version_id}/${action}`,
        { headers, data: { revision } },
      );
      expect(response.ok()).toBeTruthy();
      revision = (await response.json()).revision;
    }
    const response = await page.request.post(
      "/api/v2/learning/recall-sessions",
      { headers, data: { recall_id: row.id } },
    );
    const session = await response.json();
    await page.goto("/h5/recalls/" + row.id);
    await page.getByRole("button", { name: "开始 / 继续回忆 →" }).click();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toBeVisible();
    const activeUrl = page.url();
    const keys: string[] = [];
    const answerUrl = "**/api/v2/learning/recall-sessions/*/answers";
    await page.route(answerUrl, async (route) => {
      keys.push(route.request().headers()["idempotency-key"]);
      if (keys.length === 1)
        await route.fulfill({
          status: 503,
          headers: {
            "X-Error-Code": "RECALL_GRADING_UNAVAILABLE",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ detail: "暂时无法批改，请重试" }),
        });
      else await route.continue();
    });
    await page.getByPlaceholder("凭记忆输入答案").fill("更合适");
    await page.getByRole("button", { name: "提交本题", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "重试提交", exact: true }),
    ).toBeVisible();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveValue("更合适");
    const unchanged = await (
      await page.request.get("/api/v2/learning/recall-sessions/" + session.id)
    ).json();
    expect(unchanged.blanks[0].round_error_count).toBe(0);
    await page.getByRole("button", { name: "重试提交", exact: true }).click();
    expect(keys[0]).toBe(keys[1]);
    await page.unroute(answerUrl);
    await expect(page.locator(".recall-content")).toContainText("✓ 更合适");
    await page.locator(".recall-feedback button").click();
    await expect(page.locator(".recall-content")).toContainText("✓ 更合适");
    await page.reload();
    await expect(page.locator(".recall-content")).toContainText("✓ 更合适");
    await page.getByRole("link", { name: "返回主动回忆" }).click();
    await page.goto(activeUrl);
    await expect(page.locator(".recall-content")).toContainText("✓ 更合适");
    await page.getByPlaceholder("凭记忆输入答案").fill("硬布线");
    await page.getByRole("button", { name: "提交本题", exact: true }).click();
    await page.locator(".recall-feedback button").click();
    await expect(
      page.getByRole("heading", { name: "这份知识，你已经想起来了" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "本轮作答回顾" }),
    ).toBeVisible();
    await expect(
      page.locator(
        ".recall-content .recall-current, .recall-content .active-label, .recall-content .active-node",
      ),
    ).toHaveCount(0);
    await expect(page.locator(".recall-content")).toContainText("✓ 更合适");
    await expect(page.locator(".recall-content")).toContainText("✓ 硬布线");
  }
});

test("recall editor: paragraphs, IME, selection, order and saved versions", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/h5/");
  await page
    .getByLabel("用户名或邮箱", { exact: true })
    .fill("users-admin-" + info.project.name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(
    page.getByRole("heading", { name: "主动回忆", exact: true }),
  ).toBeVisible();
  await page.goto("/admin/recalls");
  await page.getByRole("button", { name: "新建内容", exact: true }).click();
  await page
    .getByLabel("内容标识", { exact: true })
    .fill("sentence-editor-" + info.project.name);
  await page
    .getByLabel("标题", { exact: true })
    .fill("段落编辑验收 " + info.project.name);
  await page.getByLabel("知识点搜索").fill("事务与ACID");
  await page.locator("#knowledge input").check();
  page.on("dialog", (d) => d.accept());
  await page.locator("#type").selectOption("sentence");
  const paragraph = page.getByLabel("第 1 段正文", { exact: true });
  await paragraph.fill("事务具备原子性、一致性、隔离性和持久性。");
  // Composition must not replace the active editable node or lose the final text.
  await paragraph.evaluate((element) => {
    element.dispatchEvent(
      new CompositionEvent("compositionstart", { bubbles: true }),
    );
    element.textContent += "这是中文输入结果。";
    element.dispatchEvent(
      new InputEvent("input", { bubbles: true, isComposing: true }),
    );
    element.dispatchEvent(
      new CompositionEvent("compositionend", { bubbles: true }),
    );
  });
  await expect(paragraph).toContainText("这是中文输入结果。");
  await paragraph.evaluate((element) => {
    (element as HTMLElement).focus();
    const range = document.createRange();
    range.selectNodeContents(element);
    range.collapse(false);
    const selection = getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", "粘贴的纯文本。");
    clipboardData.setData("text/html", "<img src=x><b>粘贴的纯文本。</b>");
    element.dispatchEvent(
      new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData,
      }),
    );
  });
  await expect(paragraph).toContainText("粘贴的纯文本。");
  await expect(paragraph.locator("img,b")).toHaveCount(0);
  async function selectWord(word: string) {
    const segment = page
      .locator('[data-paragraph="0"] .cell-text-segment')
      .filter({ hasText: word })
      .first();
    await segment.evaluate((element, target) => {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node: Node | null;
      while ((node = walker.nextNode())) {
        const start = node.textContent!.indexOf(target);
        if (start < 0) continue;
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, start + target.length);
        const selection = getSelection()!;
        selection.removeAllRanges();
        selection.addRange(range);
        element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
        return;
      }
      throw new Error("selection text missing");
    }, word);
    await page
      .locator('[data-paragraph="0"]')
      .getByRole("button", { name: "挖空所选文字" })
      .click();
  }
  await selectWord("原子性");
  await expect(
    page.locator('[data-paragraph="0"] .cell-segments'),
  ).toContainText("这是中文输入结果。");
  const panel = page.locator(".blank-editor[open]");
  await panel.getByLabel("第一次错误：轻提示").fill("回忆事务的整体性");
  await panel.getByLabel("第二次错误：关键线索").fill("全部完成或全部撤销");
  await selectWord("隔离性");
  await panel.getByLabel("第一次错误：轻提示").fill("关注并发事务");
  await panel.getByLabel("第二次错误：关键线索").fill("事务之间互不干扰");
  await panel.getByRole("button", { name: "提前一空" }).click();
  await expect(page.locator("[data-blank-chip]").first()).toHaveText(
    "2. 原子性",
  );
  await expect(page.locator("[data-blank-chip]").nth(1)).toHaveText(
    "1. 隔离性",
  );
  const longAnswer = "隔离性：" + "并发事务之间互不干扰，".repeat(14);
  await panel.getByLabel("标准答案", { exact: true }).fill(longAnswer);
  await expect(page.locator("[data-blank-chip]").nth(1)).toContainText(
    longAnswer,
  );
  await expect(page.locator("#preview")).toContainText(longAnswer);
  await expect(panel.locator(".blank-context")).toContainText(longAnswer);
  for (const width of [1440, 768, 375]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await expect
      .poll(() =>
        panel
          .getByLabel("标准答案", { exact: true })
          .evaluate((e) => e.scrollHeight <= e.clientHeight + 2),
      )
      .toBeTruthy();
    await page.screenshot({
      path: info.outputPath(`editor-sentence-${width}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await panel.getByLabel("标准答案", { exact: true }).fill("隔离性");
  // Selection across a blank must be rejected without changing any blanks.
  await page
    .locator('[data-paragraph="0"] .cell-segments')
    .evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
      document.dispatchEvent(new Event("selectionchange"));
    });
  await page
    .locator('[data-paragraph="0"]')
    .getByRole("button", { name: "挖空所选文字" })
    .click();
  await expect(
    page.locator('[data-paragraph="0"] .editor-error'),
  ).toContainText("不能跨越已有空位");
  await expect(page.locator("[data-blank-chip]")).toHaveCount(2);
  await panel.getByRole("button", { name: "取消挖空", exact: true }).click();
  await expect(page.locator("[data-blank-chip]")).toHaveCount(1);
  await selectWord("隔离性");
  await panel.getByLabel("第一次错误：轻提示").fill("并发");
  await panel.getByLabel("第二次错误：关键线索").fill("彼此独立");
  await page.getByRole("button", { name: "添加段落", exact: true }).click();
  await page
    .getByLabel("第 2 段正文", { exact: true })
    .fill("第二段用于检查删除。");
  await page.locator("#structure").evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
  await page
    .locator('[data-paragraph="1"]')
    .getByRole("button", { name: "挖空所选文字" })
    .click();
  await expect(
    page.locator('[data-paragraph="1"] .editor-error'),
  ).toContainText("同一段落或单元格");
  await expect(page.locator("[data-blank-chip]")).toHaveCount(2);
  await page
    .locator('[data-paragraph="0"]')
    .getByRole("button", { name: "删除段落" })
    .click();
  await expect(page.locator("[data-blank-chip]")).toHaveCount(0);
  await expect(page.locator(".blank-editor")).toHaveCount(0);
  await page
    .getByLabel("第 1 段正文", { exact: true })
    .fill("事务具有原子性。");
  await selectWord("原子性");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator("#notice")).toContainText("两级提示必须填写完整");
  await panel.getByLabel("第一次错误：轻提示").fill("整体");
  await panel.getByLabel("第二次错误：关键线索").fill("全部或全不");
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "审核通过", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await page
    .getByRole("button", {
      name: new RegExp("段落编辑验收 " + info.project.name + ".*v1"),
    })
    .click();
  await expect(page.locator("[data-blank-chip]")).toHaveText("1. 原子性");
  await expect(panel.getByLabel("第一次错误：轻提示")).toHaveValue("整体");
  await expect(
    page.getByRole("button", { name: "审核通过", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "新建内容", exact: true }).click();
  await page.locator("#type").selectOption("relation");
  await expect(
    page.getByRole("heading", { name: "节点", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "有向连线", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加推导步骤", exact: true }).click();
  await expect(page.getByText("步骤 1", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("recall: review, three hints, retest, resume, knowledge and real questions", async ({
  page,
}, info) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/h5/");
  await page
    .getByLabel("用户名或邮箱", { exact: true })
    .fill("users-admin-" + info.project.name);
  await page.getByLabel("密码", { exact: true }).fill("isolated-test-password");
  await page.getByRole("button", { name: "登录，继续学习" }).click();
  await expect(
    page.getByRole("heading", { name: "主动回忆", exact: true }),
  ).toBeVisible();
  async function expectRecallNavigation() {
    const navigation = page.getByRole("navigation", { name: "主导航" });
    await expect(navigation).toBeVisible();
    await expect(
      navigation.getByRole("link", { name: "首页", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expect
      .poll(() =>
        page.evaluate(() => {
          const nav = document.querySelector(".bottom-nav");
          const shell = document.querySelector(".with-bottom-nav");
          if (!nav || !shell) return false;
          return (
            Number.parseFloat(getComputedStyle(shell).paddingBottom) + 1 >=
            nav.getBoundingClientRect().height
          );
        }),
      )
      .toBeTruthy();
    await expect
      .poll(() =>
        page.evaluate(() => {
          const nav = document.querySelector(".bottom-nav");
          const root = document.querySelector(".recall-page");
          const last = root?.lastElementChild;
          if (!nav || !last) return false;
          window.scrollTo(0, document.documentElement.scrollHeight);
          return (
            last.getBoundingClientRect().bottom <=
            nav.getBoundingClientRect().top + 1
          );
        }),
      )
      .toBeTruthy();
  }
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 900 });
    const actions = page.locator(".recall-home-actions");
    const primary = await actions.locator(".primary").boundingBox();
    const secondary = await actions.locator(".text-button").boundingBox();
    expect(
      primary &&
        secondary &&
        Math.abs(
          primary.y + primary.height / 2 - secondary.y - secondary.height / 2,
        ) < 2,
    ).toBeTruthy();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("recall-home.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: /要点填空/ }).click();
  await expectRecallNavigation();
  const backHome = page.getByRole("link", { name: "返回首页" });
  await expect(backHome).toBeVisible();
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 900 });
    const backBox = await backHome.boundingBox();
    expect(backBox && backBox.width >= 44 && backBox.height >= 44).toBeTruthy();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: info.outputPath("recall-list.png"),
    fullPage: true,
  });
  await page.getByRole("link", { name: "开始学习 →" }).first().click();
  await expectRecallNavigation();
  await expect(page.locator(".recall-content")).toContainText("原子性");
  const detailUrl = page.url();
  await page.getByRole("link", { name: "事务与ACID" }).click();
  await expect(
    page.getByRole("heading", { name: "对应主动回忆" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "开始相关真题 →" }),
  ).toBeVisible();
  await page.goto(detailUrl);
  await page.getByRole("button", { name: "开始 / 继续回忆 →" }).click();
  await expectRecallNavigation();
  await expect(page.locator(".recall-answer")).toBeVisible();
  const sessionUrl = page.url();
  await expect(page.locator(".recall-content")).not.toContainText("原子性");
  await page.getByPlaceholder("凭记忆输入答案").fill("未完成的草稿");
  await page.getByPlaceholder("凭记忆输入答案").blur();
  await expect
    .poll(async () => {
      const r = await page.request.get(
        "/api/v2/learning/recall-sessions/" + sessionUrl.split("/").pop(),
      );
      return (await r.json()).blanks[0].draft;
    })
    .toBe("未完成的草稿");
  await page.reload();
  await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveValue(
    "未完成的草稿",
  );
  for (let i = 0; i < 3; i++) {
    await page.getByPlaceholder("凭记忆输入答案").fill("错误答案");
    await page.getByRole("button", { name: "提交本题", exact: true }).click();
    await expect(page.locator(".recall-feedback")).toBeVisible();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveCount(0);
    await expect(page.locator(".recall-slot-incorrect")).toContainText(
      "× 错误答案",
    );
    await expect(page.locator(".recall-feedback")).toContainText(
      "批改结果 · 未答对",
    );
    if (i === 0)
      await page.screenshot({
        path: info.outputPath("recall-feedback-incorrect.png"),
        fullPage: true,
      });
    if (i === 2) {
      await expect(page.locator(".recall-feedback")).toContainText("标准答案");
      await expect(page.locator(".recall-feedback")).toContainText("原子性");
    }
    await page.locator(".recall-feedback button").click();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toBeFocused();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toBeEmpty();
    if (i < 2)
      await expect(page.locator(".recall-slot-incorrect")).toHaveCount(0);
  }
  const correctAnswers = ["Consistency", "隔离性", "持久性"];
  for (const [index, answer] of correctAnswers.entries()) {
    await page.getByPlaceholder("凭记忆输入答案").fill(answer);
    await page.getByRole("button", { name: "提交本题", exact: true }).click();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveCount(0);
    await expect(page.locator(".recall-slot-correct").last()).toContainText(
      `✓ ${answer}`,
    );
    await expect(page.locator(".recall-feedback")).toContainText(
      "批改结果 · 答对",
    );
    await page.locator(".recall-feedback button").click();
    await expect(page.locator(".recall-slot-correct")).toHaveCount(index + 1);
    await expect(page.locator(".recall-slot-incorrect")).toContainText(
      "错误答案",
    );
    if (index === 0) {
      await page.reload();
      await expect(page.locator(".recall-slot-correct")).toContainText(
        "Consistency",
      );
      await expect(page.locator(".recall-slot-incorrect")).toContainText(
        "错误答案",
      );
    }
    if (index < correctAnswers.length - 1)
      await expect(page.getByPlaceholder("凭记忆输入答案")).toBeFocused();
  }
  await page.getByRole("button", { name: "开始无提示复测 →" }).click();
  await expect(page.locator(".recall-slot-result")).toHaveCount(0);
  await page.reload();
  await expect(page.locator(".recall-slot-result")).toHaveCount(0);
  await expect(page.locator(".recall-content")).not.toContainText("原子性");
  await expect(page.locator(".recall-hint")).toHaveCount(0);
  await expect(page.getByPlaceholder("凭记忆输入答案")).toBeEmpty();
  await expect(page.getByText("本轮错误 3 次 · 历史错误 3 次")).toBeVisible();
  await page.screenshot({
    path: info.outputPath("recall-retest.png"),
    fullPage: true,
  });
  await page.getByPlaceholder("凭记忆输入答案").fill("Atomicity");
  await page.getByRole("button", { name: "提交本题", exact: true }).click();
  await expect(page.locator(".recall-slot-correct")).toContainText(
    "✓ Atomicity",
  );
  await page.locator(".recall-feedback button").click();
  await expect(
    page.getByRole("heading", { name: "这份知识，你已经想起来了" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开始相关真题 →" }).click();
  await expect(page.locator(".question-part").first()).toBeVisible();
  for (const type of ["table", "relation"]) {
    await page.goto("/h5/recalls?type=" + type);
    await expectRecallNavigation();
    await page.locator(".recall-primary-link").first().click();
    await expectRecallNavigation();
    for (const width of [320, 375, 414, 768]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBeTruthy();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: info.outputPath("recall-" + type + ".png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "开始 / 继续回忆 →" }).click();
    await expect(page.getByPlaceholder("凭记忆输入答案")).toBeVisible();
    const answerUrl = "**/api/v2/learning/recall-sessions/*/answers";
    const idempotencyKeys: string[] = [];
    if (type === "table") {
      await page.route(answerUrl, async (route) => {
        idempotencyKeys.push(
          route.request().headers()["idempotency-key"] || "",
        );
        if (idempotencyKeys.length === 1) await route.abort("failed");
        else await route.continue();
      });
    }
    await page.getByPlaceholder("凭记忆输入答案").fill("形态测试答案");
    await page.getByRole("button", { name: "提交本题", exact: true }).click();
    if (type === "table") {
      await expect(
        page.getByRole("button", { name: "重试提交", exact: true }),
      ).toBeVisible();
      await expect(page.getByPlaceholder("凭记忆输入答案")).toHaveValue(
        "形态测试答案",
      );
      await page.getByRole("button", { name: "重试提交", exact: true }).click();
      expect(idempotencyKeys).toHaveLength(2);
      expect(idempotencyKeys[0]).toBeTruthy();
      expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
      await page.unroute(answerUrl);
    }
    await expect(page.locator(".recall-feedback")).toContainText(
      "批改结果 · 未答对",
    );
    const echoedAnswer =
      type === "table"
        ? page.locator(".recall-slot-incorrect").first()
        : page.locator(".recall-graph-incorrect").first();
    await expect(echoedAnswer).toContainText("形态测试答案");
    await page.locator(".recall-feedback button").click();
    await page.getByPlaceholder("凭记忆输入答案").fill("测试草稿");
    await page
      .getByRole("button", { name: "做相关真题 →", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("暂无关联的可练真题");
    await expect(page.getByRole("link", { name: "前往题库 →" })).toBeVisible();
    await page.getByRole("link", { name: "返回主动回忆" }).click();
    await expect(
      page.getByRole("heading", { name: "主动回忆", exact: true }),
    ).toBeVisible();
  }
  await page.goto("/admin/recalls");
  await expect(
    page.getByRole("heading", { name: "主动回忆", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新建内容", exact: true }).click();
  await page
    .getByLabel("内容标识", { exact: true })
    .fill("browser-" + info.project.name);
  await page
    .getByLabel("标题", { exact: true })
    .fill("浏览器审核测试 " + info.project.name);
  await page.getByLabel("知识点搜索").fill("事务与ACID");
  await page.locator("#knowledge input").check();
  const firstCell = page.getByLabel("第 1 行 1 列：知识项");
  await firstCell.fill("事务需要满足原子性、一致性、隔离性和持久性");
  await page.getByLabel("第 1 行 2 列：结论").fill("事务的四种核心性质");
  await firstCell.evaluate((element) => {
    const node = element.firstChild;
    if (!node) throw new Error("表格文字节点不存在");
    const start = element.textContent?.indexOf("隔离性") ?? -1;
    if (start < 0) throw new Error("未找到待挖空文字");
    const range = document.createRange();
    range.setStart(node, start);
    range.setEnd(node, start + 3);
    const selection = getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
  await page
    .locator('[data-cell="0-0"]')
    .getByRole("button", { name: "挖空所选文字" })
    .click();
  await expect(page.getByRole("button", { name: "1. 隔离性" })).toBeVisible();
  await page
    .getByLabel("第一次错误：轻提示")
    .first()
    .fill("回忆并发事务之间的关系");
  await page
    .getByLabel("第二次错误：关键线索")
    .first()
    .fill("多个事务之间互不干扰");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "审核通过", exact: true }),
  ).toBeVisible();
  await expect(page.locator("#preview-iframe")).toBeVisible();
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "审核通过", exact: true }).click();
  await page.getByRole("button", { name: "发布给用户", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "下架内容", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath("recall-admin.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
