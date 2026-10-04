import { computed, onScopeDispose, ref } from "vue";
import {
  VoiceInputClient,
  type FinalResult,
  type VoiceInputClientOptions,
  type VoiceInputState,
} from "@lili-voice-input/browser";
import workletUrl from "@lili-voice-input/browser/pcm-worklet.js?worker&url";
import { api } from "../api";

interface Dependencies {
  supported: () => boolean;
  session: (signal: AbortSignal) => Promise<{ token: string }>;
  client: (options: VoiceInputClientOptions) => VoiceInputClient;
}

const labels: Partial<Record<VoiceInputState, string>> = {
  "requesting-permission": "请允许使用麦克风…",
  connecting: "正在连接语音服务…",
  queued: "语音服务繁忙，正在排队…",
  recording: "正在录音，再次点击麦克风结束",
  finalizing: "正在转写并整理文字…",
};

function readableError(error: unknown): string {
  const value = error as { name?: string; message?: string; code?: string };
  if (
    value?.name === "NotAllowedError" ||
    /permission|denied/i.test(value?.message || "")
  )
    return "麦克风权限被拒绝，请在浏览器设置中允许后重试";
  if (value?.name === "NotFoundError") return "未找到可用麦克风";
  if (value?.name === "NotReadableError")
    return "麦克风正在被其他应用占用，请关闭后重试";
  return /[\u4e00-\u9fff]/.test(value?.message || "")
    ? value.message!
    : "语音输入失败，请检查麦克风和网络后重试";
}

export function useVoiceInput(
  onFinal: (result: FinalResult) => void,
  overrides: Partial<Dependencies> = {},
) {
  const dependencies: Dependencies = {
    supported: () => VoiceInputClient.isSupported(),
    session: (signal) =>
      api("/voice/session", { method: "POST", body: {}, signal }),
    client: (options) => new VoiceInputClient(options),
    ...overrides,
  };
  const active = ref(false);
  const state = ref<VoiceInputState>("idle");
  const message = ref("");
  const status = computed(
    () =>
      message.value ||
      (active.value ? labels[state.value] || "正在准备语音输入…" : ""),
  );
  let generation = 0;
  let client: VoiceInputClient | undefined;
  let controller: AbortController | undefined;

  function supported() {
    if (dependencies.supported()) return true;
    message.value = !window.isSecureContext
      ? "语音输入需要 HTTPS，或使用本机 localhost / 127.0.0.1 地址"
      : "当前浏览器不支持语音输入，请使用支持麦克风和 AudioWorklet 的浏览器";
    return false;
  }

  function cancel() {
    generation++;
    controller?.abort();
    controller = undefined;
    const previous = client;
    client = undefined;
    active.value = false;
    state.value = "idle";
    message.value = "";
    void previous?.destroy().catch(() => {});
  }

  async function start() {
    if (active.value || !supported()) return;
    cancel();
    const operation = generation;
    active.value = true;
    state.value = "connecting";
    controller = new AbortController();
    let current: VoiceInputClient | undefined;
    let received = false;
    try {
      const { token } = await dependencies.session(controller.signal);
      if (operation !== generation) return;
      const base = new URL(
        "/api/v2/voice/transcriptions",
        window.location.origin,
      );
      const ws = new URL(base + "/stream");
      ws.protocol = base.protocol === "https:" ? "wss:" : "ws:";
      current = dependencies.client({
        wsUrl: ws.href,
        fallbackUrl: base.href,
        token,
        workletUrl,
        language: "zh",
      });
      client = current;
      current.on("statechange", ({ state: next }) => {
        if (operation !== generation) return;
        state.value = next;
        if (["idle", "completed", "error"].includes(next)) active.value = false;
      });
      current.on("queued", ({ position }) => {
        if (operation === generation)
          message.value = `语音正在排队，前方 ${Math.max(0, position - 1)} 个会话`;
      });
      current.on("ready", () => {
        if (operation === generation) message.value = "";
      });
      current.on("error", (error) => {
        if (operation === generation)
          message.value = readableError(error.cause || error);
      });
      current.on("final", (result) => {
        if (operation !== generation || received) return;
        received = true;
        active.value = false;
        message.value = !result.text.trim()
          ? "未识别到有效文字，请重新录音"
          : result.polish_status === "fallback"
            ? "文本整理未完成，已保留原始转写文字"
            : result.degraded
              ? "部分语音未能识别，请检查回填文字"
              : "";
        onFinal(result);
      });
      await current.start();
    } catch (error) {
      if (operation !== generation) return;
      active.value = false;
      state.value = "error";
      message.value = readableError(error);
      client = undefined;
      await current?.destroy().catch(() => {});
    }
  }

  async function stop() {
    if (!active.value || state.value !== "recording") return;
    const operation = generation;
    try {
      await client?.stop();
    } catch (error) {
      if (operation === generation) {
        cancel();
        message.value = readableError(error);
      }
    }
  }

  const hidden = () => {
    if (document.visibilityState === "hidden" && active.value) cancel();
  };
  document.addEventListener("visibilitychange", hidden);
  onScopeDispose(() => {
    cancel();
    document.removeEventListener("visibilitychange", hidden);
  });
  return { active, state, status, message, supported, start, stop, cancel };
}
