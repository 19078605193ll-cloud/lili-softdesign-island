import { effectScope } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  FinalResult,
  VoiceInputClient,
  VoiceInputClientOptions,
} from "@lili-voice-input/browser";
import { useVoiceInput } from "./useVoiceInput";

class FakeClient {
  handlers: Record<string, ((event: any) => void)[]> = {};
  on(type: string, handler: (event: any) => void) {
    (this.handlers[type] ||= []).push(handler);
    return () => {};
  }
  emit(type: string, event: any) {
    for (const handler of this.handlers[type] || []) handler(event);
  }
  start = vi.fn(async () => {
    this.emit("statechange", { state: "recording" });
  });
  stop = vi.fn(async () => {
    this.emit("statechange", { state: "finalizing" });
    return null;
  });
  destroy = vi.fn(async () => {});
}
const final = {
  text: "识别文字",
  polish_status: "applied",
  degraded: false,
} as FinalResult;
const scopes: ReturnType<typeof effectScope>[] = [];

function setup(
  session = vi.fn(async (_signal: AbortSignal) => ({ token: "short-token" })),
) {
  const client = new FakeClient();
  const callback = vi.fn();
  const factory = vi.fn(
    (_options: VoiceInputClientOptions) =>
      client as unknown as VoiceInputClient,
  );
  const scope = effectScope();
  scopes.push(scope);
  const voice = scope.run(() =>
    useVoiceInput(callback, {
      supported: () => true,
      session,
      client: factory,
    }),
  )!;
  return { voice, client, callback, session, factory, scope };
}
beforeEach(() => {
  vi.stubGlobal("window", {
    location: { origin: "https://example.com" },
    isSecureContext: true,
  });
  vi.stubGlobal("document", {
    visibilityState: "visible",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  });
});
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop());
  vi.unstubAllGlobals();
});

describe("voice lifecycle", () => {
  it("initializes once, supplies the short token and locks until final", async () => {
    const { voice, client, session, factory } = setup();
    const starting = voice.start();
    expect(voice.active.value).toBe(true);
    await voice.start();
    await starting;
    expect(session).toHaveBeenCalledTimes(1);
    expect(factory.mock.calls[0][0]).toMatchObject({
      token: "short-token",
      wsUrl: "wss://example.com/api/v2/voice/transcriptions/stream",
    });
    expect(voice.state.value).toBe("recording");
    await voice.stop();
    await voice.stop();
    expect(client.stop).toHaveBeenCalledTimes(1);
    expect(voice.active.value).toBe(true);
    client.emit("final", final);
    expect(voice.active.value).toBe(false);
  });
  it("inserts a final once and ignores events after cancellation", async () => {
    const { voice, client, callback } = setup();
    await voice.start();
    client.emit("final", final);
    client.emit("final", final);
    expect(callback).toHaveBeenCalledTimes(1);
    voice.cancel();
    client.emit("final", final);
    expect(callback).toHaveBeenCalledTimes(1);
    expect(client.destroy).toHaveBeenCalledTimes(1);
  });
  it("ignores a cancelled bootstrap and aborts its request", async () => {
    let resolve!: (value: { token: string }) => void;
    const session = vi.fn(
      (_signal: AbortSignal) =>
        new Promise<{ token: string }>((done) => {
          resolve = done;
        }),
    );
    const { voice, factory } = setup(session);
    const starting = voice.start();
    voice.cancel();
    resolve({ token: "late-token" });
    await starting;
    expect(session.mock.calls[0][0].aborted).toBe(true);
    expect(factory).not.toHaveBeenCalled();
    expect(voice.active.value).toBe(false);
  });
  it("unlocks on unavailable service, supports retry, and reports polish fallback", async () => {
    const session = vi.fn(async (_signal: AbortSignal) => ({
      token: "short-token",
    }));
    session.mockRejectedValueOnce(new Error("语音服务尚未就绪"));
    const { voice, client, callback } = setup(session);
    await voice.start();
    expect(voice.active.value).toBe(false);
    expect(voice.status.value).toContain("尚未就绪");
    await voice.start();
    client.emit("final", { ...final, polish_status: "fallback" });
    expect(callback).toHaveBeenCalledTimes(1);
    expect(voice.status.value).toContain("原始转写");
  });
  it("releases an active recording when the component scope ends", async () => {
    const { voice, client, callback, scope } = setup();
    await voice.start();
    scope.stop();
    client.emit("final", final);
    expect(client.destroy).toHaveBeenCalledTimes(1);
    expect(callback).not.toHaveBeenCalled();
  });
});
