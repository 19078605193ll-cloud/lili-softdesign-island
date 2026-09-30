<script setup lang="ts">
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import { useLearner } from "../store";
import Icon from "../components/Icon.vue";
import Avatar from "../components/Avatar.vue";
import ProfileEditor from "../components/ProfileEditor.vue";
import Modal from "../components/Modal.vue";
const store = useLearner(),
  router = useRouter();
const stats = ref<any>(null),
  exam = ref<any>(null),
  today = ref(""),
  error = ref(""),
  busy = ref(false),
  modal = ref(""),
  draftMotto = ref("");
const plan = ref({ title: "全国统考目标", exam_date: "", start_date: "" });
let generation = 0;
async function load() {
  const current = ++generation;
  error.value = "";
  stats.value = null;
  if (!store.subjectId) return;
  try {
    const [s, p] = await Promise.all([
      api("/learning/dashboard?subject_id=" + store.subjectId),
      api("/learning/exam-plan?subject_id=" + store.subjectId),
    ]);
    if (current !== generation) return;
    stats.value = s;
    exam.value = p.plan;
    today.value = p.today;
  } catch (e: any) {
    if (current === generation) error.value = e.message;
  }
}
watch(() => store.subjectId, load, { immediate: true });
function open(which: string) {
  error.value = "";
  modal.value = which;
  draftMotto.value = store.motto;
  if (which === "plan")
    plan.value = {
      title: exam.value?.title || "全国统考目标",
      exam_date: exam.value?.exam_date || "",
      start_date: exam.value?.start_date || today.value,
    };
}
async function save() {
  busy.value = true;
  try {
    if (modal.value === "motto") {
      await api("/learning/preferences", {
        method: "PATCH",
        body: { motto: draftMotto.value },
      });
      store.motto = draftMotto.value;
    } else {
      await api("/learning/exam-plan", {
        method: "PUT",
        body: { ...plan.value, subject_id: store.subjectId },
      });
      await load();
    }
    modal.value = "";
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function choose(id: string) {
  try {
    await store.choose(id);
    modal.value = "";
  } catch (e: any) {
    error.value = e.message;
  }
}
async function logout() {
  try {
    await store.logout();
    router.replace("/login");
  } catch (e: any) {
    error.value = e.message;
  }
}
const percent = (n: any) => (n === null || n === undefined ? "—" : n + "%");
</script>
<template>
  <div class="home-page">
    <header class="home-heading">
      <button class="verse motto" @click="open('motto')">
        {{ store.motto }}<Icon name="edit" :size="16" />
      </button>
      <button
        class="round accent"
        aria-label="个人账号"
        @click="open('account')"
      >
        <Avatar :url="store.user?.avatar_url" />
      </button>
    </header>
    <section class="home-hero">
      <div class="subject-line">
        <h1>{{ store.subject?.name || "请选择科目" }}</h1>
        <button class="pill" @click="open('subject')">切换科目 ⇄</button>
      </div>
      <p class="date">
        <Icon name="calendar" />{{
          today
            ? new Date(today + "T00:00:00").toLocaleDateString("zh-CN", {
                year: "numeric",
                month: "long",
                day: "numeric",
                weekday: "long",
              })
            : "正在读取日期…"
        }}
      </p>
    </section>
    <div v-if="error && !modal" class="error" role="alert">
      {{ error }} <button @click="load">重试</button>
    </div>
    <section class="card exam-card">
      <div class="card-toolbar">
        <span>{{ exam?.title || "我的考试目标" }}</span
        ><button class="text-button" @click="open('plan')">
          <Icon name="edit" :size="18" />编辑考期
        </button>
      </div>
      <template v-if="exam"
        ><p>
          {{
            exam.days_remaining < 0
              ? "本次考试已结束"
              : exam.days_remaining === 0
                ? "今天考试，加油！"
                : "距离考试 仅剩"
          }}
        </p>
        <div class="countdown">
          {{ Math.abs(exam.days_remaining)
          }}<span>{{ exam.days_remaining < 0 ? "天前" : "天" }}</span>
        </div>
        <span class="tag neutral">{{ exam.exam_date }} 统考</span
        ><progress :value="exam.progress" max="100"></progress>
        <div class="split muted small">
          <span>备考进度 {{ exam.progress }}%</span><span>坚持就是胜利</span>
        </div></template
      >
      <div v-else class="empty">
        <p>给努力设定一个目标</p>
        <button class="text-button" @click="open('plan')">
          设置考试日期 →
        </button>
      </div>
    </section>
    <section class="section">
      <h2 class="section-label">学情看板</h2>
      <div class="stat-grid">
        <article class="card stat">
          <span>知识掌握</span><strong>{{ percent(stats?.mastery) }}</strong
          ><progress :value="stats?.mastery || 0" max="100"></progress>
        </article>
        <article class="card stat">
          <span>真题练习</span
          ><strong
            >{{ stats?.completed ?? "—"
            }}<small>/{{ stats?.total ?? "—" }}</small></strong
          ><progress
            :value="stats?.completed || 0"
            :max="stats?.total || 1"
          ></progress>
        </article>
        <article class="card stat">
          <span>综合正确率</span><strong>{{ percent(stats?.accuracy) }}</strong
          ><progress :value="stats?.accuracy || 0" max="100"></progress>
        </article>
      </div>
      <p class="metric-note">
        掌握依据：至少练习3道不同小问、最近正确率≥80%，且无待复习错题或犹豫题。
      </p>
    </section>
    <section class="section">
      <h2 class="section-label">最近学习记录</h2>
      <div class="card recent">
        <template v-if="stats?.recent.length"
          ><button
            v-for="item in stats.recent"
            :key="item.id || 'history'"
            :disabled="!item.id"
            @click="router.push('/session/' + item.id)"
          >
            <strong>{{ item.title }}</strong>
            <div>
              <span class="tag neutral">练{{ item.count }}题</span
              ><span class="tag">正确率 {{ percent(item.accuracy) }}</span>
            </div>
          </button></template
        >
        <div v-else class="empty">
          <Icon name="book" :size="30" />
          <p>
            {{ stats ? "还没有学习记录，从一道题开始吧" : "正在读取学习记录…" }}
          </p>
          <RouterLink to="/practice">开始练习 →</RouterLink>
        </div>
      </div>
    </section>

    <Modal
      v-if="modal"
      :title="
        (
          {
            account: '当前账号',
            subject: '切换科目',
            motto: '给自己的话',
            plan: '编辑考期',
          } as any
        )[modal]
      "
      @close="modal = ''"
      ><p v-if="error" class="error" role="alert">{{ error }}</p>
      <template v-if="modal === 'account'"
        ><ProfileEditor />
        <button class="primary" @click="logout">
          <Icon name="logout" />退出登录
        </button></template
      ><template v-else-if="modal === 'subject'"
        ><button
          v-for="s in store.subjects"
          :key="s.id"
          class="choice-row"
          @click="choose(s.id)"
        >
          {{ s.name
          }}<Icon v-if="store.subjectId === s.id" name="check" /></button
      ></template>
      <form v-else @submit.prevent="save">
        <label v-if="modal === 'motto'"
          >励志语<textarea
            v-model="draftMotto"
            maxlength="200"
            rows="3"
            required
          ></textarea></label
        ><template v-else
          ><label
            >考试名称<input
              v-model="plan.title"
              maxlength="100"
              required /></label
          ><label
            >开始备考<input
              type="date"
              v-model="plan.start_date"
              required /></label
          ><label
            >考试日期<input
              type="date"
              v-model="plan.exam_date"
              :min="plan.start_date"
              required /></label></template
        ><button class="primary" :disabled="busy">
          {{ busy ? "正在保存…" : "保存" }}
        </button>
      </form></Modal
    >
  </div>
</template>
