import { expect, test, type Page } from "@playwright/test";

test.use({ baseURL: "http://127.0.0.1:5179" });
const finalResult = (text: string, polish_status = "applied") => ({
  type: "final",
  text,
  polish_status,
  polished: polish_status === "applied",
  polish_reason: null,
  degraded: polish_status === "fallback",
  degraded_stage: polish_status === "fallback" ? "polish" : null,
  segment_count: 1,
  failed_segment_count: 0,
  latency_ms: 5,
  polish_latency_ms: 5,
  total_latency_ms: 10,
  admission_wait_ms: 0,
  asr_queue_wait_ms: 0,
});

async function setup(
  page: Page,
  options: {
    text?: string;
    delayed?: boolean;
    denied?: boolean;
    permissionDelayed?: boolean;
    fallback?: boolean;
    unavailable?: boolean;
  } = {},
) {
  await page.addInitScript(
    ({ denied, permissionDelayed }) => {
      const target = window as any;
      // Windows Playwright WebKit omits WebAudio and getUserMedia. Only in that
      // engine, simulate the audio boundary; Chromium exercises the real worklet.
      if (typeof AudioContext === "undefined") {
        const node = () => ({ connect() {}, disconnect() {} });
        class FixtureAudioContext {
          state = "running";
          destination = node();
          get audioWorklet() {
            return { addModule: async () => {} };
          }
          createOscillator() {
            return { ...node(), start() {} };
          }
          createMediaStreamDestination() {
            const track = {
              readyState: "live",
              stop() {
                this.readyState = "ended";
              },
            };
            return { ...node(), stream: { getTracks: () => [track] } };
          }
          createMediaStreamSource() {
            return node();
          }
          createGain() {
            return { ...node(), gain: { value: 1 } };
          }
          async resume() {}
          async close() {
            this.state = "closed";
          }
        }
        class FixtureWorkletNode {
          port: any = {
            onmessage: null,
            postMessage: () => {
              this.port.onmessage?.({ data: { type: "flushed" } });
            },
          };
          timer = setInterval(
            () =>
              this.port.onmessage?.({
                data: {
                  type: "pcm",
                  buffer: new Int16Array(1600).buffer,
                  rms: 0.2,
                },
              }),
            80,
          );
          connect() {}
          disconnect() {
            clearInterval(this.timer);
          }
        }
        target.AudioContext = FixtureAudioContext;
        target.AudioWorkletNode = FixtureWorkletNode;
        Object.defineProperty(navigator, "mediaDevices", {
          configurable: true,
          value: {},
        });
      }
      target.voiceTracks = [];
      target.voiceAudioContexts = [];
      Object.defineProperty(navigator.mediaDevices, "getUserMedia", {
        configurable: true,
        value: async () => {
          if (denied)
            throw new DOMException("Permission denied", "NotAllowedError");
          if (permissionDelayed)
            await new Promise<void>((resolve) => {
              target.resolveVoicePermission = resolve;
            });
          const context = new AudioContext();
          target.voiceAudioContexts.push(context);
          const oscillator = context.createOscillator();
          const destination = context.createMediaStreamDestination();
          oscillator.connect(destination);
          oscillator.start();
          await context.resume();
          target.voiceTracks.push(...destination.stream.getTracks());
          return destination.stream;
        },
      });
    },
    {
      denied: options.denied || false,
      permissionDelayed: options.permissionDelayed || false,
    },
  );
  await page.route("**/api/v1/auth/csrf", (route) =>
    route.fulfill({ json: { csrf_token: "fixture-csrf" } }),
  );
  await page.route("**/api/v2/voice/session", (route) =>
    route.fulfill(
      options.unavailable
        ? {
            status: 503,
            json: {
              code: "VOICE_NOT_READY",
              detail: "语音服务尚未就绪，请配置 API Key",
            },
          }
        : { json: { token: "short-fixture-token" } },
    ),
  );
  let uploads = 0,
    opens = 0,
    closes = 0,
    audioBytes = 0,
    commits = 0;
  let complete = () => {};
  await page.route("**/api/v2/voice/transcriptions", async (route) => {
    uploads++;
    expect(route.request().headers().authorization).toBe(
      "Bearer short-fixture-token",
    );
    expect(route.request().postDataBuffer()!.length).toBeGreaterThan(44);
    await route.fulfill({ json: finalResult(options.text || "备用上传结果") });
  });
  await page.routeWebSocket(
    "**/api/v2/voice/transcriptions/stream",
    (socket) => {
      opens++;
      socket.onClose(() => {
        closes++;
      });
      socket.onMessage((message) => {
        if (typeof message !== "string") {
          audioBytes += message.length;
          return;
        }
        const event = JSON.parse(message);
        if (event.type === "start") {
          expect(event.auth_token).toBe("short-fixture-token");
          socket.send(
            JSON.stringify({
              type: "ready",
              protocol_version: "1",
              session_id: "fixture",
              sample_rate: 16000,
              max_duration_seconds: 600,
              capabilities: { partial: false, http_fallback: true },
            }),
          );
        } else if (event.type === "commit") {
          commits++;
          complete = () => {
            socket.send(
              JSON.stringify(finalResult(options.text || "识别文字")),
            );
          };
          if (options.fallback)
            socket.close({ code: 1011, reason: "fixture disconnect" });
          else if (!options.delayed) complete();
        }
      });
    },
  );
  await page.goto("/h5/e2e/fixtures/voice-input.html");
  await expect(
    page.getByRole("button", { name: "语音输入", exact: true }),
  ).toBeVisible();
  return {
    complete: () => complete(),
    stats: () => ({ opens, closes, uploads, audioBytes, commits }),
  };
}

test("inserts at cursor, locks controls, preserves draft and sends only on click", async ({
  page,
}, info) => {
  const harness = await setup(page, { text: "内存分页", delayed: true });
  const input = page.getByRole("textbox", { name: "向AI提问" });
  await input.fill("解释的原理");
  await input.evaluate((element: HTMLTextAreaElement) => {
    element.focus();
    element.setSelectionRange(2, 2);
  });
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止录音" })).toBeEnabled();
  await expect(input).toHaveAttribute("readonly");
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeDisabled();
  await expect(page.locator("#quick-question")).toBeDisabled();
  await expect.poll(() => harness.stats().audioBytes).toBeGreaterThan(0);
  await page.getByRole("button", { name: "停止录音" }).click();
  await expect(page.getByRole("status")).toContainText("转写并整理");
  await expect.poll(() => harness.stats().commits).toBe(1);
  harness.complete();
  await expect(input).toHaveValue("解释内存分页的原理");
  await expect(input).not.toHaveAttribute("readonly");
  expect(
    await input.evaluate(
      (element: HTMLTextAreaElement) => element.selectionStart,
    ),
  ).toBe(6);
  await expect(page.locator("#sent")).toBeEmpty();
  expect(
    await page.evaluate(() => sessionStorage.getItem("ai-draft:voice-fixture")),
  ).toBe("解释内存分页的原理");
  await page.screenshot({ path: info.outputPath("voice-composer.png") });
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator("#sent")).toHaveText("解释内存分页的原理");
});

test("replaces selection and keeps full over-limit result", async ({
  page,
}) => {
  const harness = await setup(page, { text: "x".repeat(2001), delayed: true });
  const input = page.getByRole("textbox", { name: "向AI提问" });
  await input.fill("保留我的草稿");
  await input.evaluate((element: HTMLTextAreaElement) =>
    element.setSelectionRange(2, 4),
  );
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止录音" })).toBeEnabled();
  await page.getByRole("button", { name: "停止录音" }).click();
  await expect.poll(() => harness.stats().commits).toBe(1);
  harness.complete();
  await expect(input).toHaveValue("保留我的草稿");
  await expect(
    page.getByRole("textbox", { name: "完整语音识别结果" }),
  ).toHaveValue("x".repeat(2001));
  await expect(
    page.getByRole("button", { name: "复制识别结果" }),
  ).toBeVisible();
});

test("restores text editing after microphone denial and missing configuration", async ({
  page,
}) => {
  await setup(page, { denied: true });
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("麦克风权限被拒绝");
  await expect(
    page.getByRole("textbox", { name: "向AI提问" }),
  ).not.toHaveAttribute("readonly");
  await page.unroute("**/api/v2/voice/session");
  await page.route("**/api/v2/voice/session", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: "语音服务尚未就绪，请配置 API Key" },
    }),
  );
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("配置 API Key");
  await page.getByRole("textbox", { name: "向AI提问" }).fill("仍能手动提问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator("#sent")).toHaveText("仍能手动提问");
});

test("uses one HTTP fallback when the WebSocket disconnects", async ({
  page,
}) => {
  const harness = await setup(page, { fallback: true, text: "备用转写" });
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止录音" })).toBeEnabled();
  await expect.poll(() => harness.stats().audioBytes).toBeGreaterThan(0);
  await page.getByRole("button", { name: "停止录音" }).click();
  await expect(page.getByRole("textbox", { name: "向AI提问" })).toHaveValue(
    "备用转写",
  );
  expect(harness.stats().uploads).toBe(1);
});

test("changing draft cancels recording and stops tracks", async ({ page }) => {
  await setup(page);
  await page.getByRole("textbox", { name: "向AI提问" }).fill("上一题草稿");
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止录音" })).toBeEnabled();
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("voice-fixture-key", { detail: "other-question" }),
    ),
  );
  await expect(page.getByRole("textbox", { name: "向AI提问" })).toHaveValue("");
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).voiceTracks.every(
          (track: MediaStreamTrack) => track.readyState === "ended",
        ),
      ),
    )
    .toBe(true);
  expect(
    await page.evaluate(() => sessionStorage.getItem("ai-draft:voice-fixture")),
  ).toBe("上一题草稿");
});

test("unmount during microphone permission cannot leak a late microphone stream", async ({
  page,
}) => {
  await setup(page, { permissionDelayed: true });
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect
    .poll(() =>
      page.evaluate(() => typeof (window as any).resolveVoicePermission),
    )
    .toBe("function");
  await page.evaluate(() => {
    window.dispatchEvent(new Event("voice-fixture-unmount"));
    (window as any).resolveVoicePermission();
  });
  await expect
    .poll(() => page.evaluate(() => (window as any).voiceTracks.length))
    .toBeGreaterThan(0);
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).voiceTracks.every(
          (track: MediaStreamTrack) => track.readyState === "ended",
        ),
      ),
    )
    .toBe(true);
});

test("unsupported browsers retain ordinary text input", async ({ page }) => {
  await page.addInitScript(() => {
    (window as any).AudioContext = undefined;
    (window as any).webkitAudioContext = undefined;
  });
  await page.goto("/h5/e2e/fixtures/voice-input.html");
  await page.getByRole("button", { name: "语音输入", exact: true }).click();
  await expect(page.getByRole("status")).toContainText(
    "当前浏览器不支持语音输入",
  );
  await page.getByRole("textbox", { name: "向AI提问" }).fill("继续用文字提问");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator("#sent")).toHaveText("继续用文字提问");
});
