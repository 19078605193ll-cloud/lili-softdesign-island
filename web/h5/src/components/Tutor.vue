<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api } from "../api";
import AiText from "./AiText.vue";
import Icon from "./Icon.vue";
import AiComposer from "./AiComposer.vue";
const props = defineProps<{
  questionId: string;
  version: number;
  attemptId?: string;
  composerTarget?: string;
  readOnly?: boolean;
}>();
const emit = defineEmits<{ ready: [] }>();
const sid = ref(""),
  data = ref<any>(null),
  error = ref(""),
  busy = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined,
  disposed = false,
  started = Date.now(),
  sendKey = "",
  sendText = "",
  createKey = crypto.randomUUID();
const controller = new AbortController();
const active = (s: string) => ["queued", "running", "retry_wait"].includes(s);
async function refresh() {
  try {
    data.value = await api("/learning/tutor/sessions/" + sid.value, {
      signal: controller.signal,
    });
    if (disposed) return;
    busy.value = active(data.value.job?.status);
    emit("ready");
    if (data.value.job?.status === "failed")
      error.value = data.value.job.error || "AI回答失败，请重试";
    if (busy.value)
      timer = setTimeout(refresh, Date.now() - started < 10000 ? 2000 : 5000);
  } catch (e: any) {
    if (!disposed) {
      error.value = e.message;
      busy.value = false;
    }
  }
}
async function open() {
  busy.value = true;
  error.value = "";
  try {
    const history = await api(
      "/learning/tutor/sessions?question_id=" +
        props.questionId +
        (props.attemptId ? "&attempt_id=" + props.attemptId : ""),
    );
    const existing = history.items.find(
      (x: any) => (x.attempt_id || undefined) === props.attemptId,
    );
    if (existing) sid.value = existing.id;
    else if (props.readOnly) {
      busy.value = false;
      return;
    } else {
      const s = await api("/learning/tutor/sessions", {
        method: "POST",
        key: createKey,
        body: {
          question_id: props.questionId,
          content_version: props.version,
          attempt_id: props.attemptId,
        },
      });
      sid.value = s.id;
    }
    if (!disposed) await refresh();
  } catch (e: any) {
    if (!disposed) {
      error.value = e.message;
      busy.value = false;
    }
  }
}
async function send(value: string) {
  if (!value.trim() || busy.value) return;
  busy.value = true;
  error.value = "";
  if (value !== sendText) {
    sendText = value;
    sendKey = crypto.randomUUID();
  }
  try {
    await api("/learning/tutor/sessions/" + sid.value + "/messages", {
      method: "POST",
      key: sendKey,
      body: { text: value },
    });
    sendText = "";
    started = Date.now();
    await refresh();
    return true;
  } catch (e: any) {
    error.value = e.message;
    busy.value = false;
    return false;
  }
}
async function retry() {
  if (!sid.value) {
    await open();
    return;
  }
  if (data.value?.job?.status === "failed") {
    await send("请重新回答上一条问题。");
  } else await refresh();
}
onMounted(open);
onUnmounted(() => {
  disposed = true;
  controller.abort();
  clearTimeout(timer);
});
</script>
<template>
  <section class="card tutor-panel" data-tutor-panel>
    <header class="tutor-heading">
      <div class="robot" aria-hidden="true"><span>••</span></div>
      <div>
        <h3>你的 AI 学伴</h3>
        <p class="muted small">一步一步，想明白再向前</p>
      </div>
    </header>
    <p v-if="error" class="error" role="alert">
      {{ error }}
      <button v-if="!readOnly" @click="retry" :disabled="busy">重试</button>
    </p>
    <div class="messages" aria-live="polite">
      <article
        v-for="m in data?.messages.filter(
          (m: any, i: number) => !(i === 0 && m.role === 'user'),
        )"
        :key="m.id"
        :class="['message', m.role]"
      >
        <AiText :text="m.content" />
      </article>
      <p v-if="busy" class="muted thinking">正在思考，请稍候…</p>
    </div>
    <p v-if="readOnly && !data?.messages?.length" class="muted">
      暂无历史 AI 对话
    </p>
    <div v-if="!readOnly" class="directions">
      <button
        v-for="(direction, i) in data?.directions"
        :key="direction"
        :disabled="busy"
        @click="send(direction)"
      >
        <Icon :name="['brain', 'book', 'bulb'][Number(i)]" :size="18" /><span>{{
          direction
        }}</span
        ><Icon name="right" :size="16" />
      </button>
    </div>
    <Teleport
      v-if="!readOnly"
      :to="composerTarget || 'body'"
      :disabled="!composerTarget"
      defer
    >
      <AiComposer
        :disabled="!sid"
        :busy="busy"
        :send="send"
        :draft-key="questionId + ':' + (attemptId || 'new')"
      />
    </Teleport>
  </section>
</template>
