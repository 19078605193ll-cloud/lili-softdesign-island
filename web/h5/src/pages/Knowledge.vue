<script setup lang="ts">
import { ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "../api";
import RecallQuestions from "../components/RecallQuestions.vue";
const route = useRoute(),
  data = ref<any>(null),
  error = ref("");
let epoch = 0;
async function load() {
  const e = ++epoch;
  data.value = null;
  error.value = "";
  try {
    const d = await api("/learning/nodes/" + route.params.id);
    if (e === epoch) data.value = d;
  } catch (err: any) {
    if (e === epoch) error.value = err.message;
  }
}
watch(() => route.params.id, load, { immediate: true });
</script>
<template>
  <div class="recall-page">
    <RouterLink
      :to="
        data?.parent_id ? '/knowledge/' + data.parent_id : '/practice?tab=node'
      "
      class="back-link"
      >‹ {{ data?.parent_id ? "上级知识" : "章节列表" }}</RouterLink
    >
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="load">重试</button>
    </p>
    <template v-if="data"
      ><header class="page-hero">
        <p class="verse">从知识，到记忆，再到应用。</p>
        <h1>{{ data.name }}</h1>
        <p class="muted">{{ data.description }}</p>
      </header>
      <section v-if="data.children.length" class="card">
        <h2>下级知识</h2>
        <RouterLink
          v-for="n in data.children"
          :key="n.id"
          :to="'/knowledge/' + n.id"
          class="choice-row"
          >{{ n.name }} →</RouterLink
        >
      </section>
      <section class="card">
        <h2>对应主动回忆</h2>
        <RouterLink
          v-for="r in data.recalls"
          :key="r.id"
          :to="'/recalls/' + r.id"
          class="choice-row"
          >{{ r.title }} →</RouterLink
        >
        <p v-if="!data.recalls.length" class="muted">
          此知识点还没有已发布的回忆内容。
        </p>
      </section>
      <RecallQuestions
        :items="data.questions"
        :subject-id="data.subject_id"
        source="knowledge"
        :source-id="data.id"
    /></template>
    <div v-else-if="!error" class="empty">正在读取知识点…</div>
  </div>
</template>
