<script setup lang="ts">
import { randomUUID } from "../uuid";
import { onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api } from "../api";
import AiText from "../components/AiText.vue";
import Icon from "../components/Icon.vue";
import { useVisualViewport } from "../viewport";
const route = useRoute(),
  router = useRouter();
const viewportStyle = useVisualViewport();
const variant = ref<any>(null),
  result = ref<any>(null),
  busy = ref(true);
const error = ref(""),
  reported = ref(false),
  failedJob = ref(false);
let disposed = false,
  timer: ReturnType<typeof setTimeout> | undefined;
const controller = new AbortController();
let key = String(route.query.request || randomUUID());
const parent = String(route.query.parent || "");
const back = () => router.push("/session/" + parent);
async function showVariant(id: string) {
  const value = await api("/learning/variants/" + id, {
    signal: controller.signal,
  });
  if (disposed) return;
  variant.value = value.variant;
  result.value = value.result;
  busy.value = false;
}
async function poll(id: string) {
  try {
    const job = await api("/learning/tutor/jobs/" + id, {
      signal: controller.signal,
    });
    if (disposed) return;
    if (["queued", "running", "retry_wait"].includes(job.status)) {
      timer = setTimeout(() => poll(id), 2000);
    } else if (job.status === "succeeded") {
      await showVariant(job.result.variant_id);
    } else {
      failedJob.value = true;
      throw new Error(job.error || "类似题生成失败，请重试");
    }
  } catch (e: any) {
    if (!disposed) {
      error.value = e.message;
      busy.value = false;
      if (e.code === "VARIANT_UNAVAILABLE") failedJob.value = true;
    }
  }
}
async function prepare() {
  busy.value = true;
  error.value = "";
  if (failedJob.value) {
    key = randomUUID();
    failedJob.value = false;
  }
  await router.replace({ query: { ...route.query, request: key } });
  try {
    const value = await api(
      "/practice/questions/" + route.params.id + "/related-practice",
      {
        method: "POST",
        body: { session_id: parent },
        key,
        signal: controller.signal,
      },
    );
    if (disposed) return;
    if (value.kind === "question") {
      await router.replace({
        path: "/session/" + value.session_id,
        query: { returnSession: parent },
      });
    } else if (value.kind === "variant") await showVariant(value.variant_id);
    else await poll(value.job_id);
  } catch (e: any) {
    if (!disposed) {
      error.value = e.message;
      busy.value = false;
      if (e.code === "VARIANT_UNAVAILABLE") failedJob.value = true;
    }
  }
}
async function answer(key: string) {
  if (busy.value || result.value || reported.value) return;
  busy.value = true;
  error.value = "";
  try {
    const value = await api(
      "/learning/variants/" + variant.value.id + "/answers",
      {
        method: "POST",
        body: { answer: key },
        signal: controller.signal,
      },
    );
    if (!disposed) {
      variant.value = value.variant;
      result.value = value.result;
    }
  } catch (e: any) {
    if (!disposed) error.value = e.message;
  } finally {
    if (!disposed) busy.value = false;
  }
}
async function report() {
  busy.value = true;
  error.value = "";
  try {
    await api("/learning/variants/" + variant.value.id + "/reports", {
      method: "POST",
      body: { reason: "用户认为题干、答案或解析存在问题，请独立复核。" },
      signal: controller.signal,
    });
    if (!disposed) reported.value = true;
  } catch (e: any) {
    if (!disposed) error.value = e.message;
  } finally {
    if (!disposed) busy.value = false;
  }
}
onMounted(prepare);
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
  controller.abort();
});
</script>
<template>
  <div class="session-page related-page" :style="viewportStyle">
    <header class="session-heading">
      <button class="round" aria-label="返回原题" @click="back">
        <Icon name="left" />
      </button>
      <strong>类似题练习</strong>
    </header>
    <div class="session-content">
      <p v-if="error" class="error" role="alert">
        {{ error }}
        <button v-if="!variant" :disabled="busy" @click="prepare">重试</button>
      </p>
      <p v-if="busy && !variant" class="empty" role="status">正在准备类似题…</p>
      <section v-if="variant" class="card question-card variant-card">
        <span class="tag">AI生成教学题 · 不计正式成绩</span>
        <AiText :text="variant.stem" />
        <button
          v-for="(value, key) in variant.options"
          :key="key"
          class="option"
          :class="{
            correct: result && variant.answer === key,
            incorrect: result && result.answer === key && !result.correct,
          }"
          :disabled="busy || !!result || reported"
          @click="answer(String(key))"
        >
          <span class="option-key">{{ key }}</span
          ><AiText :text="String(value)" />
        </button>
        <div
          v-if="result"
          class="feedback"
          :class="result.correct ? 'correct' : 'incorrect'"
        >
          <strong
            >{{ result.correct ? "回答正确" : "再想想这一步" }} · 正确答案
            {{ variant.answer }}</strong
          >
          <AiText :text="variant.explanation" />
        </div>
        <button
          class="text-button report-button"
          :disabled="reported || busy"
          @click="report"
        >
          {{ reported ? "已反馈，暂停复用并复核" : "题目有误？反馈复核" }}
        </button>
      </section>
    </div>
    <footer class="session-dock">
      <button class="primary full" @click="back">返回原题</button>
    </footer>
  </div>
</template>
