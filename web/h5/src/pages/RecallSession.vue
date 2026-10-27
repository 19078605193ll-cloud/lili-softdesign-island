<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import { api, ApiError } from "../api";
import { randomUUID } from "../uuid";
import {
  recallNames,
  type RecallSession,
  type RecallFeedback,
} from "../recall";
import RecallContent from "../components/RecallContent.vue";
import Icon from "../components/Icon.vue";
const route = useRoute(),
  router = useRouter(),
  data = ref<RecallSession | null>(null),
  answer = ref(""),
  error = ref(""),
  noQuestions = ref(false),
  busy = ref(false),
  retryPending = ref(false),
  feedback = ref<RecallFeedback | null>(null),
  input = ref<HTMLInputElement>(),
  feedbackBox = ref<HTMLElement>();
const current = computed(() =>
  data.value?.blanks.find((b) => b.key === data.value?.current_key),
);
const presentedKey = computed(
  () => feedback.value?.blank_key || data.value?.current_key || null,
);
const presentedNumber = computed(() => {
  const index = data.value?.blanks.findIndex(
    (b) => b.key === presentedKey.value,
  );
  return index === undefined || index < 0 ? null : index + 1;
});
const slotPresentations = computed(() => {
  const slots = { ...data.value?.slot_presentations };
  // Hide this blank's old mistake while the learner makes another attempt.
  if (!feedback.value && data.value?.current_key)
    delete slots[data.value.current_key];
  return slots;
});
const feedbackTitle = computed(() => {
  if (!feedback.value) return "";
  if (feedback.value.correct)
    return feedback.value.needs_retest
      ? "本题答对，稍后无提示复测"
      : "本题答对，已经掌握";
  return feedback.value.answer
    ? "本题未答对，先记住标准答案"
    : "本题未答对，根据提示再试一次";
});
const feedbackAction = computed(() => {
  if (!feedback.value) return "继续";
  if (!feedback.value.correct && !feedback.value.answer)
    return "根据提示再答一次";
  if (data.value?.phase === "retest_ready") return "查看复测安排";
  if (data.value?.phase === "completed") return "查看完成结果";
  return "继续下一题";
});
let timer: ReturnType<typeof setTimeout> | undefined,
  savePromise: Promise<void> | null = null,
  epoch = 0;
let pending: {
  key: string;
  body: { revision: number; blank_key: string; answer: string };
} | null = null;
let invalidated = false;
function reportError(err: any) {
  if (err instanceof ApiError && err.status === 404) {
    const recallId = data.value?.recall_id || route.query.recall_id;
    invalidated = true;
    clearTimeout(timer);
    pending = null;
    feedback.value = null;
    answer.value = "";
    data.value = null;
    void router.replace({
      path: recallId ? "/recalls/" + recallId : "/recalls",
      query: { updated: "1" },
    });
    return;
  }
  error.value = err.message;
}
async function checkPublication() {
  if (!data.value || busy.value || invalidated || document.hidden) return;
  const sid = data.value.id;
  try {
    await api<RecallSession>("/learning/recall-sessions/" + sid);
  } catch (err) {
    // Transient connectivity must not interrupt an answer in progress.
    if (data.value?.id === sid && err instanceof ApiError && err.status === 404)
      reportError(err);
  }
}
let publicationTimer: ReturnType<typeof setInterval>;
onMounted(() => {
  publicationTimer = setInterval(checkPublication, 15000);
  window.addEventListener("focus", checkPublication);
  document.addEventListener("visibilitychange", checkPublication);
});
async function load() {
  const e = ++epoch;
  error.value = "";
  try {
    const d = await api<RecallSession>(
      "/learning/recall-sessions/" + route.params.id,
    );
    if (e !== epoch) return;
    data.value = d;
    answer.value = d.blanks.find((b) => b.key === d.current_key)?.draft || "";
    feedback.value = null;
    retryPending.value = false;
    pending = null;
    await nextTick();
    if (e === epoch) input.value?.focus();
  } catch (err: any) {
    if (e === epoch) reportError(err);
  }
}
watch(() => route.params.id, load, { immediate: true });
async function save() {
  clearTimeout(timer);
  if (savePromise) await savePromise;
  if (
    invalidated ||
    !data.value?.current_key ||
    data.value.read_only ||
    busy.value ||
    feedback.value ||
    pending
  )
    return;
  const value = answer.value;
  if (value === (current.value?.draft || "")) return;
  const sid = data.value.id,
    body = {
      revision: data.value.revision,
      blank_key: data.value.current_key,
      answer: value,
    };
  savePromise = (async () => {
    const d = await api<RecallSession>("/learning/recall-sessions/" + sid, {
      method: "PATCH",
      body,
    });
    if (!invalidated) data.value = d;
  })();
  try {
    await savePromise;
  } catch (err) {
    reportError(err);
    if (!invalidated) throw err;
  } finally {
    savePromise = null;
  }
}
function changed() {
  clearTimeout(timer);
  timer = setTimeout(() => {
    save().catch((e) => (error.value = e.message));
  }, 600);
}
async function submit() {
  if (!data.value || !current.value || busy.value) return;
  clearTimeout(timer);
  if (savePromise) {
    try {
      await savePromise;
    } catch (e: any) {
      reportError(e);
      return;
    }
  }
  if (invalidated || !data.value) return;
  busy.value = true;
  error.value = "";
  try {
    pending ??= {
      key: randomUUID(),
      body: {
        revision: data.value.revision,
        blank_key: current.value.key,
        answer: answer.value,
      },
    };
    const submittedValue = pending.body.answer;
    const r = await api<{ session: RecallSession; feedback: RecallFeedback }>(
      "/learning/recall-sessions/" + data.value.id + "/answers",
      { method: "POST", body: pending.body, key: pending.key },
    );
    data.value = r.session;
    feedback.value = r.feedback;
    answer.value = submittedValue;
    retryPending.value = false;
    pending = null;
    await nextTick();
    feedbackBox.value?.focus();
  } catch (e: any) {
    reportError(e);
    if (invalidated) return;
    if (e instanceof ApiError && e.status < 500) {
      pending = null;
      retryPending.value = false;
    } else {
      retryPending.value = true;
      if (pending) answer.value = pending.body.answer;
    }
  } finally {
    busy.value = false;
  }
}
async function acknowledge() {
  feedback.value = null;
  answer.value = current.value?.draft || "";
  await nextTick();
  input.value?.focus();
}
async function retest() {
  if (!data.value) return;
  busy.value = true;
  error.value = "";
  try {
    data.value = await api<RecallSession>(
      "/learning/recall-sessions/" + data.value.id + "/retest",
      { method: "POST", body: { revision: data.value.revision } },
    );
    feedback.value = null;
    retryPending.value = false;
    answer.value = "";
    await nextTick();
    input.value?.focus();
  } catch (e: any) {
    reportError(e);
  } finally {
    busy.value = false;
  }
}
async function questions() {
  if (!data.value || busy.value) return;
  try {
    await save();
    if (invalidated || !data.value) return;
    busy.value = true;
    noQuestions.value = false;
    const related = await api<{ total: number }>(
      "/learning/recalls/" +
        data.value.recall_id +
        "/questions?limit=1&version_id=" +
        data.value.version_id,
    );
    if (!related.total) {
      noQuestions.value = true;
      busy.value = false;
      return;
    }
    const s = await api<{ id: string }>("/learning/sessions", {
      method: "POST",
      body: {
        subject_id: data.value.subject_id,
        source: "recall",
        source_id: data.value.version_id,
        mode: "resume",
      },
    });
    busy.value = false;
    await router.push("/session/" + s.id);
  } catch (e: any) {
    reportError(e);
    busy.value = false;
  }
}
onBeforeRouteLeave(async () => {
  if (invalidated) return true;
  if (busy.value || pending) {
    error.value = "请先完成当前提交，网络失败时可重试原提交。";
    return false;
  }
  try {
    await save();
    return true;
  } catch (e: any) {
    error.value = "草稿未保存：" + e.message;
    return false;
  }
});
onBeforeUnmount(() => {
  ++epoch;
  clearTimeout(timer);
  clearInterval(publicationTimer);
  window.removeEventListener("focus", checkPublication);
  document.removeEventListener("visibilitychange", checkPublication);
});
</script>
<template>
  <div class="recall-page recall-training">
    <RouterLink to="/recalls" class="back-link" aria-label="返回主动回忆"
      ><Icon name="left" :size="22" /><span>主动回忆</span></RouterLink
    >
    <p v-if="error" class="error" role="alert">
      {{ error }}
      <button v-if="!retryPending" @click="load" :disabled="busy">
        重新加载
      </button>
    </p>
    <template v-if="data"
      ><header class="recall-session-header">
        <div class="recall-session-meta">
          <span class="tag neutral">{{ recallNames[data.type] }}</span>
          <span>{{ data.phase === "retest" ? "无提示复测" : "回忆训练" }}</span>
        </div>
        <h1>{{ data.title }}</h1>
        <div class="recall-session-progress">
          <span>已掌握 {{ data.completed }} / {{ data.total }} 空</span>
          <span v-if="presentedNumber">当前第 {{ presentedNumber }} 空</span>
          <button
            class="text-button"
            :disabled="busy || data.read_only || !!feedback"
            @click="questions"
          >
            做相关真题 →
          </button>
        </div>
        <progress :value="data.completed" :max="data.total" />
      </header>
      <section v-if="noQuestions" class="card" role="status">
        暂无关联的可练真题。
        <RouterLink to="/practice">前往题库 →</RouterLink>
      </section>
      <p v-if="data.read_only" class="card">
        此内容已下架，当前仅可查看历史进度。
      </p>
      <section
        class="card recall-context-panel"
        aria-labelledby="recall-context-title"
      >
        <header class="recall-panel-heading">
          <div>
            <h2 id="recall-context-title">
              {{
                feedback
                  ? "查看本题批改"
                  : current
                    ? "只回忆当前空位"
                    : "本轮作答回顾"
              }}
            </h2>
            <p>
              {{
                feedback
                  ? "你的答案已回填到原来的空位中。"
                  : current
                    ? "先看上下文，再凭记忆补出高亮空位。"
                    : "本轮已提交的答案保留在对应空位中。"
              }}
            </p>
          </div>
          <span v-if="presentedNumber"
            >第 {{ presentedNumber }} / {{ data.total }} 空</span
          >
        </header>
        <RecallContent
          :content="data.content"
          :blanks="data.blanks"
          :current-key="presentedKey"
          :slot-presentations="slotPresentations"
        />
      </section>
      <section
        v-if="feedback"
        ref="feedbackBox"
        class="card recall-feedback"
        :class="feedback.correct ? 'is-correct' : 'is-incorrect'"
        aria-live="polite"
        tabindex="-1"
      >
        <div class="recall-feedback-status">
          <span class="recall-feedback-icon" aria-hidden="true">
            <Icon :name="feedback.correct ? 'check' : 'x'" :size="22" />
          </span>
          <div>
            <p>
              {{ feedback.correct ? "批改结果 · 答对" : "批改结果 · 未答对" }}
            </p>
            <h2>{{ feedbackTitle }}</h2>
          </div>
        </div>
        <p class="recall-feedback-location">
          你的答案已显示在上方第 {{ presentedNumber }} 个空位。
        </p>
        <div v-if="feedback.hint" class="recall-feedback-detail">
          <strong>本次提示</strong>
          <p>{{ feedback.hint }}</p>
        </div>
        <div v-if="feedback.answer" class="recall-feedback-detail">
          <strong>标准答案</strong>
          <p>{{ feedback.answer }}</p>
        </div>
        <button class="primary" @click="acknowledge">
          {{ feedbackAction }} →
        </button>
      </section>
      <form
        v-else-if="current && !data.read_only"
        class="card recall-answer"
        :aria-busy="busy"
        @submit.prevent="submit"
      >
        <div class="recall-answer-heading">
          <span>当前题</span>
          <strong>{{ current.label }}</strong>
        </div>
        <p v-if="current.hint" class="recall-hint">{{ current.hint }}</p>
        <label :for="'answer-' + current.key">你的答案</label>
        <input
          :id="'answer-' + current.key"
          ref="input"
          v-model="answer"
          @input="changed"
          @blur="save().catch((e) => (error = e.message))"
          :readonly="busy || retryPending"
          :aria-describedby="`answer-help-${current.key}`"
          placeholder="凭记忆输入答案"
          autocomplete="off"
          maxlength="2000"
          required
        />
        <div class="split">
          <p class="muted small">
            本轮错误 {{ current.round_error_count }} 次 · 历史错误
            {{ current.historical_error_count }} 次
          </p>
          <button
            type="submit"
            class="primary"
            :disabled="busy || !answer.trim()"
          >
            {{ busy ? "正在批改…" : retryPending ? "重试提交" : "提交本题" }}
          </button>
        </div>
        <p :id="`answer-help-${current.key}`" class="recall-answer-help">
          {{
            retryPending
              ? "刚才未提交成功，答案已保留。请重试原提交。"
              : "提交后会逐题批改；看过提示的题会在最后无提示复测。"
          }}
        </p>
      </form>
      <section
        v-else-if="data.phase === 'retest_ready' && !data.read_only"
        class="card recall-complete"
      >
        <h2>再回忆一次，让记忆更牢</h2>
        <p>
          本次有
          {{ data.blanks.filter((b) => b.needs_retest).length }}
          个空需要无提示答对。旧答案和提示会全部收起。
        </p>
        <button class="primary" :disabled="busy" @click="retest">
          开始无提示复测 →
        </button>
      </section>
      <section
        v-else-if="data.phase === 'completed'"
        class="card recall-complete"
      >
        <h2>这份知识，你已经想起来了</h2>
        <p>{{ data.total }} 个空均已无提示答对。去真题中检验一下。</p>
        <button
          class="primary"
          :disabled="busy || data.read_only"
          @click="questions"
        >
          开始相关真题 →</button
        ><RouterLink to="/recalls" class="text-button"
          >选择下一份知识 →</RouterLink
        >
      </section>
    </template>
    <div v-else-if="!error" class="empty">正在恢复训练…</div>
  </div>
</template>
