<script setup lang="ts">
import { ref, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { recallNames, type RecallDetail, type RecallSession } from "../recall";
import RecallContent from "../components/RecallContent.vue";
import RecallQuestions from "../components/RecallQuestions.vue";
import Icon from "../components/Icon.vue";
const route = useRoute(),
  router = useRouter(),
  data = ref<RecallDetail | null>(null),
  questions = ref<any[]>([]),
  error = ref(""),
  busy = ref(false),
  questionBox = ref<HTMLElement>();
let epoch = 0;
async function load() {
  const e = ++epoch;
  data.value = null;
  error.value = "";
  try {
    const d = await api<RecallDetail>("/learning/recalls/" + route.params.id);
    const q = await api<{ items: any[] }>(
      "/learning/recalls/" + route.params.id + "/questions?limit=100",
    );
    if (e !== epoch) return;
    data.value = d;
    questions.value = q.items;
    await nextTick();
    if (route.query.practice)
      questionBox.value?.scrollIntoView({ block: "start" });
  } catch (err: any) {
    if (e === epoch) error.value = err.message;
  }
}
watch(() => route.params.id, load, { immediate: true });
async function start() {
  if (!data.value) return;
  busy.value = true;
  try {
    const s = await api<RecallSession>("/learning/recall-sessions", {
      method: "POST",
      body: { recall_id: data.value.id },
    });
    await router.push("/recall-session/" + s.id);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="recall-page recall-detail-page">
    <RouterLink to="/recalls" class="back-link" aria-label="返回主动回忆"
      ><Icon name="left" :size="22" /><span>主动回忆</span></RouterLink
    >
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="load">重试</button>
    </p>
    <template v-if="data"
      ><header class="recall-detail-header">
        <span class="tag neutral">{{ recallNames[data.type] }}</span>
        <h1>{{ data.title }}</h1>
        <p>先理解完整知识，再隐藏答案逐题回忆。</p>
      </header>
      <section
        class="card recall-knowledge-panel"
        aria-labelledby="recall-knowledge-title"
      >
        <header class="recall-panel-heading">
          <div>
            <h2 id="recall-knowledge-title">完整知识</h2>
            <p>阅读时先关注知识之间的关系，不必立即背诵。</p>
          </div>
          <span>{{ data.blanks.length }} 个回忆空位</span>
        </header>
        <RecallContent :content="data.content" :blanks="data.blanks" full />
        <div v-if="data.knowledge.length" class="recall-knowledge-links">
          <span>关联知识点</span>
          <div>
            <RouterLink
              v-for="k in data.knowledge"
              :key="k.id"
              :to="'/knowledge/' + k.id"
              >{{ k.name }} ↗</RouterLink
            >
          </div>
        </div>
        <div class="recall-detail-actions">
          <button class="primary" :disabled="busy" @click="start">
            {{ busy ? "正在进入…" : "开始 / 继续回忆" }} →</button
          ><a href="#recall-questions" class="text-button">查看相关真题 ↓</a>
        </div>
      </section>
      <div id="recall-questions" ref="questionBox" class="recall-related-block">
        <RecallQuestions
          :items="questions"
          :subject-id="data.subject_id"
          source="recall"
          :source-id="data.version_id"
        /></div
    ></template>
    <div v-else-if="!error" class="empty">正在读取知识…</div>
  </div>
</template>
