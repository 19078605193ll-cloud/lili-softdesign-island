<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { playCelebration } from "../celebration";
import type { StreakMilestone } from "../composables/useAnswerStreak";
const props = defineProps<{ level: StreakMilestone }>();
const emit = defineEmits<{ finished: [] }>();
const canvas = ref<HTMLCanvasElement>();
const visible = ref(true);
let stop: (() => void) | undefined;
let noticeTimer: ReturnType<typeof setTimeout>;
let finishTimer: ReturnType<typeof setTimeout>;
onMounted(() => {
  if (canvas.value) stop = playCelebration(canvas.value, props.level);
  noticeTimer = setTimeout(() => {
    visible.value = false;
  }, 1200);
  finishTimer = setTimeout(
    () => emit("finished"),
    { 3: 1200, 5: 1800, 10: 2200 }[props.level],
  );
});
onUnmounted(() => {
  stop?.();
  clearTimeout(noticeTimer);
  clearTimeout(finishTimer);
});
</script>

<template>
  <div class="streak-celebration">
    <canvas ref="canvas" hidden aria-hidden="true" />
    <div v-if="visible" class="streak-notice" role="status">
      连对 {{ level }} 题
    </div>
  </div>
</template>

<style scoped>
.streak-celebration {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 30;
}
canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.streak-notice {
  position: absolute;
  top: calc(max(12px, env(safe-area-inset-top)) + 64px);
  left: 50%;
  transform: translateX(-50%);
  padding: 8px 16px;
  border: 1px solid var(--color-line);
  border-radius: var(--radius-pill);
  background: var(--color-jade);
  color: var(--color-accent-dark);
  font-weight: 600;
  white-space: nowrap;
  pointer-events: none;
}
</style>
