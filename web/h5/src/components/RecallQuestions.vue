<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
const props = defineProps<{
  items: any[];
  subjectId: string;
  source: "recall" | "knowledge";
  sourceId: string;
}>();
const router = useRouter(),
  busy = ref(false),
  error = ref("");
async function start() {
  busy.value = true;
  error.value = "";
  try {
    const s = await api<{ id: string }>("/learning/sessions", {
      method: "POST",
      body: {
        subject_id: props.subjectId,
        source: props.source,
        source_id: props.sourceId,
        mode: "resume",
      },
    });
    await router.push("/session/" + s.id);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="card recall-questions">
    <h2>相关真题</h2>
    <p class="muted small">按已确认的知识点关联推荐，可随时开始。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="items.length"
      ><button class="primary" :disabled="busy" @click="start">
        {{ busy ? "正在进入…" : "开始相关真题" }} →
      </button>
      <ol>
        <li v-for="q in items" :key="q.id">
          <strong>{{ q.source.title }} · 第 {{ q.source.label }} 题</strong>
          <p class="muted small">
            {{ q.matched_knowledge.map((n: any) => n.name).join(" / ") }}
          </p>
        </li>
      </ol></template
    >
    <div v-else class="empty">
      暂无关联的可练真题。<RouterLink to="/practice">前往题库 →</RouterLink>
    </div>
  </section>
</template>
