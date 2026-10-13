<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import RecallContent from "../components/RecallContent.vue";
import type { RecallContent as Content, RecallBlank, RecallType } from "../recall";

interface PreviewData {
  title: string;
  type: RecallType;
  content: Content;
  blanks: RecallBlank[];
}

const route = useRoute();
const data = ref<PreviewData | null>(null);
const error = ref("");
const mode = ref<"full" | "masked" | "step">("full");
const position = ref(0);
const currentKey = computed(() =>
  mode.value === "step" ? data.value?.blanks[position.value]?.key || null : null,
);

onMounted(async () => {
  try {
    data.value = await api(
      "/api/v2/admin/recalls/versions/" + route.params.id + "/preview",
    );
  } catch (value) {
    error.value = value instanceof Error ? value.message : "预览加载失败";
  }
});
</script>

<template>
  <div class="recall-page recall-preview-page">
    <p v-if="error" class="notice error">{{ error }}</p>
    <template v-else-if="data">
      <header class="recall-preview-header">
        <div>
          <span class="tag neutral">草稿预览</span>
          <h1>{{ data.title }}</h1>
        </div>
        <div class="preview-modes" aria-label="预览模式">
          <button :class="{ active: mode === 'full' }" @click="mode = 'full'">
            完整答案
          </button>
          <button :class="{ active: mode === 'masked' }" @click="mode = 'masked'">
            全部挖空
          </button>
          <button :class="{ active: mode === 'step' }" @click="mode = 'step'">
            逐空训练
          </button>
        </div>
      </header>
      <section class="card">
        <RecallContent
          :content="data.content"
          :blanks="data.blanks"
          :full="mode === 'full'"
          :current-key="currentKey"
        />
      </section>
      <div v-if="mode === 'step' && data.blanks.length" class="preview-stepper">
        <button
          aria-label="上一个空位"
          :disabled="position === 0"
          @click="position--"
        >
          上一个
        </button>
        <span>{{ position + 1 }} / {{ data.blanks.length }}</span>
        <button
          aria-label="下一个空位"
          :disabled="position >= data.blanks.length - 1"
          @click="position++"
        >
          下一个
        </button>
      </div>
    </template>
    <p v-else class="muted">正在加载...</p>
  </div>
</template>

<style scoped>
.recall-preview-page { padding: 20px; }
.recall-preview-header { display: flex; justify-content: space-between; gap: 16px; align-items: end; margin-bottom: 16px; }
.recall-preview-header h1 { margin: 8px 0 0; font-size: 24px; }
.preview-modes { display: flex; border: 1px solid var(--color-line); border-radius: 6px; overflow: hidden; }
.preview-modes button { border: 0; border-right: 1px solid var(--color-line); border-radius: 0; background: white; color: var(--color-ink); min-height: 40px; }
.preview-modes button:last-child { border-right: 0; }
.preview-modes button.active { background: var(--color-accent); color: white; }
.preview-stepper { display: flex; align-items: center; justify-content: center; gap: 16px; }
@media (max-width: 640px) {
  .recall-preview-page { padding: 12px; }
  .recall-preview-header { display: block; }
  .preview-modes { margin-top: 12px; }
  .preview-modes button { flex: 1; padding-inline: 8px; }
}
</style>
