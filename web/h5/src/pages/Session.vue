<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import { api, ApiError } from "../api";
import Icon from "../components/Icon.vue";
import RichText from "../components/RichText.vue";
import Tutor from "../components/Tutor.vue";
import { useVisualViewport } from "../viewport";
import { useLearner } from "../store";
const route = useRoute(),
  router = useRouter(),
  store = useLearner(),
  session = ref<any>(null),
  question = ref<any>(null),
  result = ref<any>(null),
  answers = ref<Record<string, string>>({}),
  marks = ref<string[]>([]),
  error = ref(""),
  busy = ref(false),
  loading = ref(false),
  tutor = ref(false),
  explanation = ref(false),
  conflict = ref(false);
const viewportStyle = useVisualViewport();
const scroller = ref<HTMLElement>();
const notice = ref("");
let noticeTimer: ReturnType<typeof setTimeout> | undefined;
const viewKey = () =>
  "session-view:" + session.value?.id + ":" + session.value?.position;
let restoredScroll = 0;
let restorePending = false;
function saveView() {
  if (!session.value || loading.value) return;
  sessionStorage.setItem(
    viewKey(),
    JSON.stringify({
      scroll: scroller.value?.scrollTop || 0,
      tutor: tutor.value,
      explanation: explanation.value,
    }),
  );
}
async function restoreScroll(tutorReady = false) {
  if (!restorePending) return;
  await nextTick();
  if (scroller.value) scroller.value.scrollTop = restoredScroll;
  if (!tutor.value || tutorReady) restorePending = false;
}
onBeforeRouteLeave(saveView);
const availablePositions = computed<number[]>(
  () =>
    session.value?.available_positions ||
    session.value?.question_ids.map((_: unknown, i: number) => i) ||
    [],
);
const canMove = (delta: number) =>
  availablePositions.value.includes(session.value?.position) &&
  availablePositions.value[
    availablePositions.value.indexOf(session.value.position) + delta
  ] !== undefined;
let epoch = 0;
let submissionKey = "";
let pending: { body: any; key: string } | null = null;
let disposed = false;
const allAnswered = computed(() =>
  question.value?.parts.every((p: any) => answers.value[p.id]),
);
const correct = computed(() =>
  result.value?.parts.every((p: any) => p.correct),
);
const content = computed(() => result.value?.snapshot || question.value);
async function load() {
  clearTimeout(noticeTimer);
  notice.value = "";
  const token = ++epoch;
  loading.value = true;
  error.value = "";
  question.value = null;
  result.value = null;
  tutor.value = false;
  explanation.value = false;
  conflict.value = false;
  pending = null;
  try {
    const s = await api("/learning/sessions/" + route.params.id);
    if (token !== epoch) return;
    session.value = s;
    if (s.available_positions && !s.available_positions.length) {
      return;
    }
    const qid = s.question_ids[s.position];
    const aid = s.attempts[qid];
    const [source, m] = await Promise.all([
      api(aid ? "/learning/attempts/" + aid : "/practice/questions/" + qid),
      api("/learning/questions/" + qid + "/marks"),
    ]);
    if (token !== epoch) return;
    const q = aid ? source.snapshot : source;
    question.value = q;
    marks.value = m.items;
    answers.value = {};
    const draft = s.drafts[qid];
    if (draft) {
      if (draft.content_version === q.content_version)
        answers.value = draft.answers;
      else error.value = "题目已更新，旧草稿未套用。请重新作答。";
    }
    if (aid) {
      result.value = source;
      answers.value = Object.fromEntries(
        result.value.parts.map((p: any) => [p.question_id, p.answer]),
      );
      tutor.value =
        s.read_only ||
        !result.value.parts.every((p: any) => p.correct) ||
        marks.value.includes("hesitant");
    }
    const saved = JSON.parse(sessionStorage.getItem(viewKey()) || "null");
    if (saved) {
      tutor.value = saved.tutor || tutor.value;
      explanation.value = saved.explanation;
    }
    restoredScroll = saved?.scroll || 0;
    restorePending = true;
    submissionKey =
      sessionStorage.getItem("submit:" + s.id + ":" + qid) ||
      crypto.randomUUID();
    sessionStorage.setItem("submit:" + s.id + ":" + qid, submissionKey);
  } catch (e: any) {
    if (token === epoch) error.value = e.message;
  } finally {
    if (token === epoch) {
      loading.value = false;
      notice.value = session.value?.notice || "";
      if (notice.value) noticeTimer = setTimeout(() => { notice.value = ""; }, 1000);
      await restoreScroll();
    }
  }
}
watch(() => route.params.id, load, { immediate: true });
onUnmounted(() => {
  clearTimeout(noticeTimer);
  disposed = true;
  epoch++;
});
async function draft() {
  return api("/learning/sessions/" + session.value.id, {
    method: "PATCH",
    body: {
      question_id: question.value.id,
      content_version: question.value.content_version,
      answers: answers.value,
    },
  });
}
async function select(part: string, key: string) {
  if (busy.value || result.value || pending) return;
  answers.value = { ...answers.value, [part]: key };
  if (question.value.parts.length === 1) {
    await submit();
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    await draft();
  } catch (e: any) {
    error.value = e.message;
    conflict.value = e instanceof ApiError && e.status === 409;
  } finally {
    busy.value = false;
  }
}
async function submit() {
  if (busy.value || !allAnswered.value || result.value) return;
  busy.value = true;
  error.value = "";
  pending ||= {
    key: submissionKey,
    body: {
      practice_question_id: question.value.id,
      content_version: question.value.content_version,
      answers: { ...answers.value },
      session_id: session.value.id,
    },
  };
  try {
    const r = await api("/learning/attempts", { method: "POST", ...pending });
    result.value = await api("/learning/attempts/" + r.id);
    pending = null;
    session.value.attempts[question.value.id] = r.id;
    const m = await api("/learning/questions/" + question.value.id + "/marks");
    marks.value = m.items;
    tutor.value = !correct.value;
  } catch (e: any) {
    error.value = e.message;
    conflict.value = e instanceof ApiError && e.status === 409;
  } finally {
    busy.value = false;
  }
}
async function move(delta: number) {
  if (busy.value || !canMove(delta)) return;
  saveView();
  const position =
    availablePositions.value[
      availablePositions.value.indexOf(session.value.position) + delta
    ];
  sessionStorage.removeItem(
    "session-view:" + session.value.id + ":" + position,
  );
  busy.value = true;
  error.value = "";
  try {
    await api("/learning/sessions/" + session.value.id, {
      method: "PATCH",
      body: { position },
    });
    await load();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
function back() {
  router.push(
    ["wrong", "favorite", "hesitant"].includes(session.value?.source)
      ? "/notebook"
      : route.query.returnSession
        ? "/session/" + route.query.returnSession
        : session.value?.source === "node" ? "/practice?tab=node" : "/practice",
  );
}
async function favorite() {
  try {
    const active = marks.value.includes("favorite");
    await api("/learning/questions/" + question.value.id + "/marks/favorite", {
      method: active ? "DELETE" : "PUT",
      body: active ? undefined : { attempt_id: result.value?.id },
    });
    marks.value = active
      ? marks.value.filter((x) => x !== "favorite")
      : [...marks.value, "favorite"];
  } catch (e: any) {
    error.value = e.message;
  }
}
async function hesitate() {
  try {
    await api("/learning/questions/" + question.value.id + "/marks/hesitant", {
      method: "PUT",
      body: { attempt_id: result.value?.id },
    });
    if (!marks.value.includes("hesitant")) marks.value.push("hesitant");
    tutor.value = true;
  } catch (e: any) {
    error.value = e.message;
  }
}
function similar() {
  if (busy.value) return;
  saveView();
  router.push({
    path: "/related/" + question.value.id,
    query: { parent: session.value.id },
  });
}
function optionClass(p: any, key: string) {
  if (!result.value) return answers.value[p.id] === key ? "chosen" : "";
  const correctKeys = p.correct_option_keys || [];
  if (correctKeys.includes(key)) return "correct";
  if (answers.value[p.id] === key) return "incorrect";
  return "";
}
</script>
<template>
  <div class="session-page" :style="viewportStyle">
    <header class="session-heading">
      <button class="round" aria-label="返回列表" @click="back">
        <Icon name="left" /></button
      ><strong>{{ session?.title || "题目练习" }}</strong
      ><span class="tag"
        >{{ session ? session.position + 1 : "—" }}/{{
          session?.question_ids.length || "—"
        }}题</span
      >
    </header>
    <div ref="scroller" class="session-content">
      <p v-if="notice && !loading" class="session-note muted small" role="status">{{ notice }}</p>
      <section v-if="!loading && session?.available_positions?.length === 0" class="empty">
        <h2>这份练习暂时告一段落</h2>
        <p>已保存你的学习记录，可以选择其他试卷继续练习。</p>
        <RouterLink to="/practice">去选一套试卷 →</RouterLink>
      </section>
      <div v-if="error" class="error" role="alert">
        {{ error }}
        <button v-if="conflict || !question" @click="load">重新加载</button
        ><button
          v-else-if="!result && allAnswered"
          @click="submit"
          :disabled="busy"
        >
          重试提交
        </button>
      </div>
      <p v-if="loading" class="empty">正在读取题目…</p>
      <template v-if="content && !loading"
        ><section class="card question-card">
          <div class="card-toolbar">
            <div class="inline">
              <span class="tag">{{
                content.type === "composite" ? "组合题" : "单选题"
              }}</span
              ><span class="muted small"
                >{{ content.source.year }} ·
                {{
                  content.source.source_type === "recalled"
                    ? "考生回忆版"
                    : "历年真题"
                }}</span
              >
            </div>
            <button
              class="icon-button"
              :class="{ favorited: marks.includes('favorite') }"
              :aria-label="marks.includes('favorite') ? '取消收藏' : '收藏题目'"
              :disabled="session.read_only"
              @click="favorite"
            >
              <Icon name="star" :size="26" />
            </button>
          </div>
          <RichText
            v-if="content.material_html"
            :html="content.material_html"
          />
          <section
            v-for="(part, index) in content.parts"
            :key="part.id"
            class="question-part"
          >
            <h3 v-if="content.parts.length > 1" class="part-label">
              小问 {{ Number(index) + 1 }} · 原题 {{ part.question_no }}
            </h3>
            <div class="stem"><RichText :html="part.stem_html" /></div>
            <div v-if="result && !correct" class="answer-summary">
              <span>你的答案：{{ answers[part.id] }}</span
              ><span>正确答案：{{ part.correct_option_keys.join("、") }}</span>
            </div>
            <button
              v-for="o in part.options"
              :key="o.key"
              class="option"
              :class="optionClass(part, o.key)"
              :disabled="busy || !!result || conflict || !!pending"
              @click="select(part.id, o.key)"
            >
              <span class="option-key">{{ o.key }}</span
              ><RichText :html="o.content_html" /><Icon
                v-if="result && part.correct_option_keys.includes(o.key)"
                name="check"
              /><Icon
                v-else-if="result && answers[part.id] === o.key"
                name="x"
              />
            </button>
          </section>
          <button
            v-if="!result && content.parts.length > 1"
            class="primary full"
            :disabled="!allAnswered || busy || conflict"
            @click="submit"
          >
            {{ busy ? "正在提交…" : "提交整题答案" }}
          </button>
          <p v-if="busy && !result" class="muted small">正在保存，请稍候…</p>
        </section>
        <template v-if="result"
          ><section class="feedback" :class="correct ? 'correct' : 'incorrect'">
            <div class="inline">
              <Icon :name="correct ? 'check' : 'x'" /><strong>{{
                correct ? "回答正确" : "本题答错了，再梳理一下思路"
              }}</strong>
            </div>
            <template v-if="correct"
              ><RichText
                v-if="content.explanation_html"
                :html="content.explanation_html" /><RichText
                v-for="part in content.parts"
                :key="part.id"
                :html="part.explanation_html"
            /></template>
            <p
              v-if="correct && ['wrong', 'hesitant'].includes(session.source)"
              class="small"
            >
              已解除本题的错题和犹豫状态，收藏保持不变。
            </p>
          </section>
          <div v-if="!correct" class="tabs answer-tabs">
            <button
              :class="{ selected: !explanation }"
              @click="
                explanation = false;
                tutor = true;
              "
            >
              <Icon name="bulb" />先想想哪里没懂</button
            ><button
              :class="{ selected: explanation }"
              @click="explanation = true"
            >
              <Icon name="book" />直接看解析
            </button>
          </div>
          <section v-if="explanation && !correct" class="card explanation">
            <h3>题目解析</h3>
            <RichText :html="content.explanation_html" />
            <div v-for="part in content.parts" :key="part.id">
              <RichText :html="part.explanation_html" />
            </div>
            <button class="text-button" @click="tutor = true">
              {{ session.read_only ? "查看历史 AI 对话" : "继续向 AI 提问 →" }}
            </button>
          </section>
          <button
            v-if="correct && !tutor && !session.read_only"
            class="hesitant-link"
            @click="hesitate"
          >
            本题有疑问，问问 AI <Icon name="right" :size="16" /></button
        ></template>
        <Tutor
          v-if="tutor"
          :key="question.id + ':' + (result?.id || 'new')"
          :question-id="question.id"
          :version="question.content_version"
          :attempt-id="result?.id"
          :read-only="session.read_only"
          composer-target="#session-composer"
          @ready="restoreScroll(true)"
        />
        <p v-if="marks.includes('hesitant')" class="muted small">
          已标记为犹豫题
        </p>
      </template>
    </div>
    <div class="session-dock">
      <div id="session-composer"></div>
      <button
        v-if="result && !loading && !session.read_only"
        class="primary full similar"
        :disabled="busy"
        @click="similar"
      >
        <Icon name="refresh" :size="18" />再练一道类似题<Icon
          name="arrow"
          :size="18"
        />
      </button>
      <footer v-if="session" class="question-nav">
        <button :disabled="busy || loading || !canMove(-1)" @click="move(-1)">
          <Icon name="left" />上一题
        </button>
        <button v-if="canMove(1)" :disabled="busy || loading" @click="move(1)">
          下一题<Icon name="right" />
        </button>
        <button v-else :disabled="busy || loading" @click="back">
          返回列表<Icon name="right" />
        </button>
      </footer>
    </div>
  </div>
</template>
