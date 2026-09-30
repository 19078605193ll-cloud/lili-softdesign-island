<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import { useLearner } from "../store";
import Icon from "../components/Icon.vue";
const store = useLearner(),
  router = useRouter(),
  route = useRoute(),
  tab = computed({
    get: () => route.query.tab === "node" ? "node" : "paper",
    set: (value: string) => { void router.replace({ query: value === "node" ? { tab: "node" } : {} }); },
  }),
  query = ref(""),
  items = ref<any[]>([]),
  error = ref(""),
  busy = ref(false),
  next = ref<number | null>(null);
let epoch = 0;
async function load(append = false) {
  const current = ++epoch;
  busy.value = true;
  error.value = "";
  if (!append) items.value = [];
  try {
    const base = query.value.trim()
      ? "/practice/search?subject_id=" +
        store.subjectId +
        "&q=" +
        encodeURIComponent(query.value.trim())
      : tab.value === "paper"
        ? "/practice/papers?subject_id=" + store.subjectId
        : "/learning/chapters?subject_id=" + store.subjectId;
    const r = await api(base + (append ? "&cursor=" + next.value : ""));
    if (current !== epoch) return;
    items.value = append ? [...items.value, ...r.items] : r.items;
    next.value = r.next_cursor ?? null;
  } catch (e: any) {
    if (current === epoch) error.value = e.message;
  } finally {
    if (current === epoch) busy.value = false;
  }
}
watch([tab, () => store.subjectId], () => load(), { immediate: true });
async function start(item: any) {
  busy.value = true;
  error.value = "";
  try {
    const s = await api("/learning/sessions", {
      method: "POST",
      body: {
        subject_id: store.subjectId,
        source: query.value.trim() ? "node" : tab.value,
        source_id: item.id,
        mode: "resume",
      },
    });
    router.push("/session/" + s.id);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="practice-page">
    <header class="page-hero">
      <RouterLink to="/" class="back-link"><Icon name="left" />首页</RouterLink>
      <p class="verse">专注每一次练习，<br />让进步看得见。</p>
      <h1>软设岛</h1>
      <p class="muted subtitle">{{ store.subject?.name }} · 题库练习</p>
    </header>
    <form class="search-box" @submit.prevent="load()">
      <Icon name="search" /><input
        aria-label="搜索章节或知识点"
        v-model="query"
        placeholder="搜索章节或知识点"
      /><button type="submit">搜索</button
      ><button
        v-if="query"
        type="button"
        @click="
          query = '';
          load();
        "
        aria-label="清空搜索"
      >
        ×
      </button>
    </form>
    <section class="card practice-panel">
      <div class="tabs">
        <button
          :class="{ selected: tab === 'paper' }"
          @click="
            query = '';
            tab = 'paper';
          "
        >
          <Icon name="book" />真题</button
        ><button
          :class="{ selected: tab === 'node' }"
          @click="
            query = '';
            tab = 'node';
          "
        >
          <Icon name="bookmark" />章节
        </button>
      </div>
      <div class="section-heading">
        <h2>
          {{
            query.trim()
              ? "搜索结果"
              : tab === "paper"
                ? "历年真题"
                : "章节列表"
          }}
        </h2>
        <span class="muted small">{{
          tab === "paper" ? "按套练习，温故知新" : "按章练习，逐个击破"
        }}</span>
      </div>
      <p v-if="error" class="error" role="alert">
        {{ error }} <button @click="load()">重试</button>
      </p>
      <div v-if="busy && !items.length" class="empty">正在加载题库…</div>
      <div v-else-if="!items.length && !error" class="empty">
        {{ query ? "没有找到相关章节或知识点" : "暂无已发布题目" }}
      </div>
      <button
        v-for="item in items"
        :key="item.id"
        class="list-card"
        :disabled="busy"
        @click="start(item)"
      >
        <div class="split">
          <strong>{{ item.name || item.title }}</strong
          ><span
            v-if="item.state"
            class="tag"
            :class="{
              warning: item.state === '正在提升',
              danger: item.state === '需要加强',
            }"
            >{{ item.state }}</span
          >
        </div>
        <div class="list-meta">
          <span>{{
            item.total !== undefined
              ? item.source
              : item.score_share === null
                ? "暂无分值统计"
                : "历史分值占比 " + item.score_share + "%"
          }}</span
          ><progress
            :value="
              item.total !== undefined ? item.completed : item.mastery || 0
            "
            :max="item.total || 100"
          ></progress
          ><span>{{
            item.total !== undefined
              ? `已答${item.completed}/${item.total}`
              : item.mastery === null || item.mastery === undefined
                ? "—"
                : item.mastery + "%"
          }}</span
          ><Icon name="right" :size="18" />
        </div>
        <small
          v-if="
            item.resume_position !== null && item.resume_position !== undefined
          "
          class="muted"
          >上次停在第{{ item.resume_position + 1 }}题</small
        >
        <small
          v-if="item.score_share !== null && item.sample_label"
          class="muted"
          >{{ item.sample_label }}</small
        ></button
      ><button
        v-if="next !== null"
        class="text-button more"
        :disabled="busy"
        @click="load(true)"
      >
        加载更多
      </button>
    </section>
  </div>
</template>
