<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useLearner } from "../store";
import { api } from "../api";
import { recallNames, type RecallList } from "../recall";
import Icon from "../components/Icon.vue";
const route = useRoute(),
  store = useLearner(),
  data = ref<RecallList | null>(null),
  error = ref("");
let epoch = 0;
async function load() {
  const e = ++epoch;
  error.value = "";
  data.value = null;
  try {
    const type = ["table", "sentence", "relation"].includes(
      String(route.query.type),
    )
      ? "&type=" + route.query.type
      : "";
    const r = await api<RecallList>(
      "/learning/recalls?subject_id=" + store.subjectId + type,
    );
    if (e === epoch) data.value = r;
  } catch (err: any) {
    if (e === epoch) error.value = err.message;
  }
}
watch([() => store.subjectId, () => route.query.type], load, {
  immediate: true,
});
const groups = computed(() => {
  const items = data.value?.items || [];
  if (route.query.practice)
    return [
      {
        key: "practice",
        title: "选择知识内容",
        description: "从对应知识进入已关联的历年真题。",
        items,
      },
    ];
  return [
    {
      key: "progress",
      title: "继续学习",
      description: "接着上次的位置，一次只完成一个空位。",
      items: items.filter((item) => item.session_id && !item.completed),
    },
    {
      key: "new",
      title: "待开始",
      description: "先读完整知识，再进入回忆训练。",
      items: items.filter((item) => !item.session_id && !item.completed),
    },
    {
      key: "completed",
      title: "已完成",
      description: "这些内容已通过无提示复测。",
      items: items.filter((item) => item.completed),
    },
  ].filter((group) => group.items.length);
});
const completedCount = computed(
  () => data.value?.items.filter((item) => item.completed).length || 0,
);
function detailTarget(item: RecallList["items"][number]) {
  return `/recalls/${item.id}${route.query.practice ? "?practice=1" : ""}`;
}
function primaryTarget(item: RecallList["items"][number]) {
  return item.session_id && !item.completed && !route.query.practice
    ? `/recall-session/${item.session_id}`
    : detailTarget(item);
}
function primaryLabel(item: RecallList["items"][number]) {
  if (route.query.practice) return "查看相关真题";
  if (item.session_id && !item.completed) return "继续回忆";
  if (item.completed) return "查看知识";
  return "开始学习";
}
function stateLabel(item: RecallList["items"][number]) {
  if (item.completed) return "已完成";
  if (item.session_id)
    return item.pending ? `待复测 ${item.pending} 空` : "学习中";
  return "未开始";
}
</script>
<template>
  <div class="recall-page recall-list-page">
    <p v-if="route.query.updated" class="recall-note" role="status">
      原训练已更新或不存在，请选择最新内容重新学习。
    </p>
    <RouterLink to="/" class="back-link" aria-label="返回首页"
      ><Icon name="left" :size="22" /><span>首页</span></RouterLink
    >
    <header class="recall-page-title">
      <div>
        <p class="recall-kicker">想得起来，才能用得出来。</p>
        <h1>主动回忆</h1>
      </div>
      <p class="muted">
        {{
          route.query.practice
            ? "选择知识内容，直接进入相关真题。"
            : "选择一份知识，按自己的节奏逐题想起来。"
        }}
      </p>
    </header>
    <dl
      v-if="data?.items.length && !route.query.practice"
      class="recall-summary"
      aria-label="主动回忆进度概览"
    >
      <div>
        <dt>知识内容</dt>
        <dd>{{ data.items.length }} 份</dd>
      </div>
      <div>
        <dt>已掌握</dt>
        <dd>{{ data.mastered }} 空</dd>
      </div>
      <div>
        <dt>已完成</dt>
        <dd>{{ completedCount }} 份</dd>
      </div>
    </dl>
    <nav class="recall-filter" aria-label="按主动回忆类型筛选">
      <RouterLink
        :to="{
          path: '/recalls',
          query: route.query.practice ? { practice: '1' } : {},
        }"
        :class="{ selected: !route.query.type }"
        >全部</RouterLink
      ><RouterLink
        v-for="(name, type) in recallNames"
        :key="type"
        :to="{ path: '/recalls', query: { ...route.query, type } }"
        :class="{ selected: route.query.type === type }"
        >{{ name }}</RouterLink
      >
    </nav>
    <p v-if="error" class="error" role="alert">
      {{ error }} <button @click="load">重试</button>
    </p>
    <div v-else-if="!data" class="empty">正在读取内容…</div>
    <div v-else-if="!data.items.length" class="card empty">
      {{
        data.enabled
          ? "此分类还没有已发布的主动回忆内容。"
          : "主动回忆暂未开放。"
      }}<RouterLink to="/practice">去做真题 →</RouterLink>
    </div>
    <div v-else class="recall-groups">
      <section
        v-for="group in groups"
        :key="group.key"
        class="recall-list-section"
        :aria-labelledby="`recall-group-${group.key}`"
      >
        <header class="recall-section-heading">
          <div>
            <h2 :id="`recall-group-${group.key}`">{{ group.title }}</h2>
            <p>{{ group.description }}</p>
          </div>
          <span>{{ group.items.length }} 份</span>
        </header>
        <div class="recall-catalog">
          <article
            v-for="item in group.items"
            :key="item.id"
            class="card recall-catalog-card"
          >
            <div class="recall-card-main">
              <div class="recall-card-meta">
                <span class="tag neutral">{{ recallNames[item.type] }}</span>
                <span>{{ stateLabel(item) }}</span>
              </div>
              <h3>{{ item.title }}</h3>
              <div class="recall-progress-copy">
                <span
                  >已掌握 {{ item.mastered }} / {{ item.total_blanks }} 空</span
                >
                <span v-if="item.pending">{{ item.pending }} 空待复测</span>
              </div>
              <progress :value="item.mastered" :max="item.total_blanks" />
            </div>
            <div class="recall-card-actions">
              <RouterLink class="recall-primary-link" :to="primaryTarget(item)"
                >{{ primaryLabel(item) }} →</RouterLink
              >
              <RouterLink
                v-if="
                  item.session_id && !item.completed && !route.query.practice
                "
                class="recall-secondary-link"
                :to="detailTarget(item)"
                >查看知识</RouterLink
              >
            </div>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>
