<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "../api";
import { useLearner } from "../store";
import type { RecallList } from "../recall";
import Icon from "./Icon.vue";
const store = useLearner(),
  data = ref<RecallList | null>(null),
  error = ref("");
let epoch = 0;
async function load() {
  const e = ++epoch;
  data.value = null;
  error.value = "";
  if (!store.subjectId) return;
  try {
    const r = await api<RecallList>(
      "/learning/recalls?subject_id=" + store.subjectId,
    );
    if (epoch === e) data.value = r;
  } catch (err: any) {
    if (epoch === e) error.value = err.message;
  }
}
watch(() => store.subjectId, load, { immediate: true });
</script>
<template>
  <section v-if="data?.enabled !== false" class="card recall-home">
    <h2>
      <span class="recall-brain"><Icon name="brain" :size="28" /></span>主动回忆
    </h2>
    <div class="recall-types">
      <RouterLink to="/recalls?type=table"
        ><Icon name="table" /><span
          >对比记忆<small>对照关键差异</small></span
        ></RouterLink
      >
      <RouterLink to="/recalls?type=sentence"
        ><Icon name="lines" /><span
          >要点填空<small>补全定义规则</small></span
        ></RouterLink
      >
      <RouterLink to="/recalls?type=relation"
        ><Icon name="relation" /><span
          >关系推演<small>顺着关系推出</small></span
        ></RouterLink
      >
    </div>
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="load">重试</button>
    </p>
    <div v-else class="recall-home-actions">
      <RouterLink
        class="primary"
        :to="
          data?.resume_session_id
            ? '/recall-session/' + data.resume_session_id
            : '/recalls'
        "
        >{{
          data?.resume_phase === "retest_ready"
            ? "复测待掌握内容"
            : data?.resume_session_id
              ? "继续今日回忆"
              : "开始今日回忆"
        }}
        <Icon name="arrow" :size="20"
      /></RouterLink>
      <RouterLink
        class="text-button"
        :to="
          data?.resume_recall_id
            ? '/recalls/' + data.resume_recall_id + '?practice=1'
            : '/recalls?practice=1'
        "
        >直接做相关真题 <Icon name="right" :size="16"
      /></RouterLink>
    </div>
    <p
      v-if="data && (data.mastered || data.pending)"
      class="muted small recall-home-progress"
    >
      已掌握 {{ data.mastered }} 空 · 完成 {{ data.completed }} 份 · 待复测 {{ data.pending }} 空
    </p>
  </section>
</template>
