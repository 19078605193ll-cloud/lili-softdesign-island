<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import Icon from "./Icon.vue";
import { useVoiceInput } from "../composables/useVoiceInput";
import { insertVoiceText } from "../voiceText";
const props = defineProps<{
  disabled: boolean;
  busy: boolean;
  send: (value: string) => Promise<boolean | undefined>;
  draftKey: string;
}>();
const emit = defineEmits<{ voiceActive: [value: boolean] }>();
const text = ref(sessionStorage.getItem("ai-draft:" + props.draftKey) || "");
const input = ref<HTMLTextAreaElement>();
const overflow = ref("");
let selection = { start: 0, end: 0, draft: "", key: "" };
const voice = useVoiceInput(async (result) => {
  if (selection.key !== props.draftKey || !result.text.trim()) return;
  const inserted = insertVoiceText(
    selection.draft,
    result.text,
    selection.start,
    selection.end,
  );
  if (inserted.overflow) {
    overflow.value = result.text;
    return;
  }
  text.value = inserted.text;
  await nextTick();
  if (selection.key !== props.draftKey) return;
  input.value?.focus();
  input.value?.setSelectionRange(inserted.cursor, inserted.cursor);
});
const { active: voiceActive, state: voiceState, status: voiceStatus } = voice;
onBeforeUnmount(() => {
  voice.cancel();
  emit("voiceActive", false);
});
watch(voiceActive, (value) => emit("voiceActive", value), { flush: "sync" });
watch(
  () => props.draftKey,
  (key) => {
    voice.cancel();
    overflow.value = "";
    text.value = sessionStorage.getItem("ai-draft:" + key) || "";
  },
  { flush: "sync" },
);
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) voice.cancel();
  },
);
async function toggleVoice() {
  if (voiceState.value === "recording") {
    await voice.stop();
    return;
  }
  if (voiceActive.value || props.busy || props.disabled || !voice.supported())
    return;
  overflow.value = "";
  selection = {
    start: input.value?.selectionStart ?? text.value.length,
    end: input.value?.selectionEnd ?? text.value.length,
    draft: text.value,
    key: props.draftKey,
  };
  await voice.start();
}
async function copyTranscript() {
  try {
    await navigator.clipboard.writeText(overflow.value);
    voice.message.value = "识别结果已复制";
  } catch {
    voice.message.value = "请选中识别结果后手动复制";
  }
}
function resize() {
  if (!input.value) return;
  input.value.style.height = "auto";
  input.value.style.height = Math.min(input.value.scrollHeight, 136) + "px";
}
watch(
  text,
  async (value) => {
    sessionStorage.setItem("ai-draft:" + props.draftKey, value);
    await nextTick();
    resize();
  },
  { flush: "sync" },
);
onMounted(resize);
async function submit() {
  if (props.busy || props.disabled || voiceActive.value || !text.value.trim())
    return;
  const sent = text.value;
  if (await props.send(sent)) {
    if (text.value === sent) text.value = "";
  }
}
</script>
<template>
  <div class="ai-composer">
    <p v-if="voiceStatus" class="voice-status small" role="status">
      {{ voiceStatus }}
    </p>
    <div v-if="overflow" class="voice-overflow">
      <p class="small">
        识别结果超过输入框的 2000 字限制，草稿已保留。请复制后分段发送。
      </p>
      <textarea
        :value="overflow"
        readonly
        aria-label="完整语音识别结果"
        rows="3"
      />
      <button type="button" @click="copyTranscript">复制识别结果</button>
      <button type="button" @click="overflow = ''">关闭</button>
    </div>
    <form class="ai-input" @submit.prevent="submit">
      <textarea
        ref="input"
        v-model="text"
        rows="1"
        maxlength="2000"
        aria-label="向AI提问"
        placeholder="把不清楚的地方告诉我…"
        :disabled="disabled"
        :readonly="voiceActive"
      />
      <button
        type="button"
        class="voice-button"
        :class="{ recording: voiceState === 'recording' }"
        :disabled="
          disabled || busy || (voiceActive && voiceState !== 'recording')
        "
        :aria-label="voiceState === 'recording' ? '停止录音' : '语音输入'"
        :title="voiceState === 'recording' ? '停止录音' : '语音输入'"
        :aria-pressed="voiceState === 'recording'"
        @pointerdown.prevent
        @click="toggleVoice"
      >
        <Icon :name="voiceState === 'recording' ? 'stop' : 'mic'" :size="20" />
      </button>
      <button
        type="submit"
        class="primary"
        :disabled="busy || disabled || voiceActive || !text.trim()"
      >
        发送
      </button>
    </form>
  </div>
</template>
