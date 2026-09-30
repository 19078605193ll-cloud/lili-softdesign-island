<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from "vue";
const props = defineProps<{
  disabled: boolean;
  busy: boolean;
  send: (value: string) => Promise<boolean | undefined>;
  draftKey: string;
}>();
const text = ref(sessionStorage.getItem("ai-draft:" + props.draftKey) || "");
const input = ref<HTMLTextAreaElement>();
function resize() {
  if (!input.value) return;
  input.value.style.height = "auto";
  input.value.style.height = Math.min(input.value.scrollHeight, 136) + "px";
}
watch(text, async (value) => {
  sessionStorage.setItem("ai-draft:" + props.draftKey, value);
  await nextTick();
  resize();
});
onMounted(resize);
async function submit() {
  if (props.busy || props.disabled || !text.value.trim()) return;
  const sent = text.value;
  if (await props.send(sent)) {
    if (text.value === sent) text.value = "";
  }
}
</script>
<template>
  <form class="ai-input" @submit.prevent="submit">
    <textarea
      ref="input"
      v-model="text"
      rows="1"
      maxlength="2000"
      aria-label="向AI提问"
      placeholder="把不清楚的地方告诉我…"
      :disabled="disabled"
    />
    <button class="primary" :disabled="busy || disabled || !text.trim()">
      发送
    </button>
  </form>
</template>
