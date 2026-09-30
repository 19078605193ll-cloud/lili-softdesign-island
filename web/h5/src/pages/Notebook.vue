<script setup lang="ts">
import { ref, watch, onUnmounted, nextTick } from "vue";
import { useRouter, onBeforeRouteLeave } from "vue-router";
import { api } from "../api";
import { useLearner } from "../store";
import Icon from "../components/Icon.vue";
import Modal from "../components/Modal.vue";
import Tutor from "../components/Tutor.vue";
import RichText from "../components/RichText.vue";
const history = ref<any>(null);
async function showHistory(item: any) {
  try {
    history.value = await api("/learning/attempts/" + item.attempt_id);
  } catch (e: any) {
    error.value = e.message;
  }
}
const router = useRouter(),
  store = useLearner(),
  tab = ref(sessionStorage.getItem("notebook-tab") || "wrong"),
  items = ref<any[]>([]),
  error = ref(""),
  busy = ref(false),
  next = ref<number | null>(null),
  ai = ref<any>(null);
let epoch = 0;
let restoring = true;
const savedScroll = Number(sessionStorage.getItem("notebook-scroll") || 0);
async function load(append = false) {
  const token = ++epoch;
  busy.value = true;
  error.value = "";
  if (!append) items.value = [];
  try {
    const r = await api(
      "/learning/notebook?subject_id=" +
        store.subjectId +
        "&type=" +
        tab.value +
        (append ? "&cursor=" + next.value : ""),
    );
    if (token !== epoch) return;
    items.value = append ? [...items.value, ...r.items] : r.items;
    next.value = r.next_cursor;
    if (restoring) {
      const savedCount = Number(sessionStorage.getItem("notebook-count") || 20);
      if (next.value !== null && items.value.length < savedCount) {
        await load(true);
        return;
      }
      restoring = false;
      await nextTick();
      window.scrollTo(0, savedScroll);
    }
  } catch (e: any) {
    error.value = e.message;
  } finally {
    if (token === epoch) busy.value = false;
  }
}
watch(
  tab,
  (_, previous) => {
    sessionStorage.setItem("notebook-tab", tab.value);
    if (previous !== undefined) {
      restoring = false;
      sessionStorage.setItem("notebook-scroll", "0");
      sessionStorage.setItem("notebook-count", "20");
    }
    load();
  },
  { immediate: true },
);
onBeforeRouteLeave(() => {
  sessionStorage.setItem("notebook-scroll", String(window.scrollY));
  sessionStorage.setItem("notebook-count", String(items.value.length));
});
onUnmounted(() => {
  epoch++;
});
async function start(item: any) {
  busy.value = true;
  try {
    const s = await api("/learning/sessions", {
      method: "POST",
      body: {
        subject_id: store.subjectId,
        source: tab.value,
        source_id: item.practice_question_id,
      },
    });
    router.push("/session/" + s.id);
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function remove(item: any) {
  busy.value = true;
  try {
    await api(
      "/learning/questions/" +
        item.practice_question_id +
        "/marks/" +
        tab.value,
      { method: "DELETE" },
    );
    await load();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="notebook-page">
    <header class="page-hero">
      <RouterLink to="/" class="back-link" aria-label="返回首页"
        ><Icon name="left"
      /></RouterLink>
      <h1>错题本</h1>
      <p class="verse">
        集中复习薄弱之处，<br />让每一次错误都成为进步的起点。
      </p>
    </header>
    <div class="tabs notebook-tabs">
      <button
        v-for="(label, key) in {
          wrong: '错题',
          favorite: '收藏题',
          hesitant: '犹豫题',
        }"
        :key="key"
        :class="{ selected: tab === key }"
        @click="tab = key"
      >
        {{ label }}
      </button>
    </div>
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="load()">重试</button>
    </p>
    <p v-if="busy && !items.length" class="empty">正在读取记录…</p>
    <section v-else-if="!items.length" class="card empty">
      <Icon name="bookmark" :size="32" />
      <h2>这里还没有题目</h2>
      <p class="muted">
        {{
          tab === "wrong"
            ? "每一次练习，都让理解更进一步。"
            : "在答题页标记后，可在这里集中复习。"
        }}
      </p>
      <RouterLink to="/practice">去刷题 →</RouterLink>
    </section>
    <article
      v-for="item in items"
      :key="item.practice_question_id"
      class="card notebook-card"
    >
      <div class="notebook-copy">
        <span class="tag">{{ item.chapter_name }}</span>
        <h2 class="line-clamp" :title="item.stem_excerpt">
          {{ item.stem_excerpt }}
        </h2>
        <span v-if="!item.can_practice" class="muted small"
          >题目暂不可重练，历史记录仍保留</span
        >
      </div>
      <div class="notebook-actions">
        <button v-if="item.attempt_id" class="pill" @click="showHistory(item)">
          查看历史答案
        </button>
        <button
          class="pill"
          :disabled="busy || !item.can_practice"
          @click="start(item)"
        >
          <Icon name="refresh" :size="17" />再做一次</button
        ><button
          class="pill ai-pill"
          :disabled="!item.can_practice && !item.attempt_id"
          @click="ai = item"
        >
          <Icon name="chat" :size="17" />AI问答</button
        ><button
          class="text-button resolve"
          :disabled="busy"
          @click="remove(item)"
        >
          {{ tab === "favorite" ? "取消收藏" : "解除" }}
        </button>
      </div>
    </article>
    <button
      v-if="next !== null"
      class="text-button more"
      :disabled="busy"
      @click="load(true)"
    >
      加载更多</button
    ><Modal v-if="ai" title="AI问答" @close="ai = null"
      ><Tutor
        :question-id="ai.practice_question_id"
        :version="ai.content_version"
        :attempt-id="ai.attempt_id || undefined"
        :read-only="!ai.can_practice"
    /></Modal>
    <Modal v-if="history" title="历史答案" @close="history = null">
      <RichText
        v-if="history.snapshot.material_html"
        :html="history.snapshot.material_html"
      />
      <section v-for="part in history.snapshot.parts" :key="part.id">
        <RichText :html="part.stem_html" />
        <div v-for="option in part.options" :key="option.key">
          <strong>{{ option.key }}</strong
          ><RichText :html="option.content_html" />
        </div>
        <p>
          你的答案：{{
            history.parts.find((p: any) => p.question_id === part.id)?.answer
          }}；正确答案：{{ part.correct_option_keys.join("、") }}
        </p>
        <RichText :html="part.explanation_html" />
      </section>
      <RichText :html="history.snapshot.explanation_html" />
    </Modal>
  </div>
</template>
