<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import { api } from "../api";
import { useLearner } from "../store";
import Icon from "./Icon.vue";
import Avatar from "./Avatar.vue";
const store = useLearner(),
  editing = ref(false),
  username = ref(""),
  nameError = ref(""),
  avatarError = ref(""),
  success = ref(""),
  busy = ref(false);
const picker = ref<HTMLInputElement>(),
  file = ref<File>(),
  imageUrl = ref(""),
  width = ref(0),
  height = ref(0),
  zoom = ref(1),
  cx = ref(0),
  cy = ref(0);
let timer: ReturnType<typeof setTimeout> | undefined;
const cropSize = computed(
  () => Math.min(width.value, height.value) / zoom.value,
);
const previewStyle = computed(() => {
  const scale = 240 / cropSize.value;
  return {
    width: width.value * scale + "px",
    height: height.value * scale + "px",
    left: 120 - cx.value * scale + "px",
    top: 120 - cy.value * scale + "px",
  };
});
function clamp() {
  const half = cropSize.value / 2;
  cx.value = Math.max(half, Math.min(width.value - half, cx.value));
  cy.value = Math.max(half, Math.min(height.value - half, cy.value));
}
function notify(text: string) {
  success.value = text;
  clearTimeout(timer);
  timer = setTimeout(() => (success.value = ""), 1000);
}
function cancelCrop() {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value);
  imageUrl.value = "";
  file.value = undefined;
  points.clear();
}
async function choose(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0];
  (event.target as HTMLInputElement).value = "";
  if (!selected) return;
  avatarError.value = "";
  if (
    !["image/jpeg", "image/png", "image/webp"].includes(selected.type) ||
    selected.size > 5 * 1024 * 1024
  ) {
    avatarError.value = "请选择不超过5MB的JPEG、PNG或WebP图片";
    return;
  }
  const url = URL.createObjectURL(selected),
    image = new Image();
  image.src = url;
  try {
    await image.decode();
    if (image.naturalWidth * image.naturalHeight > 20000000)
      throw Error("图片不能超过2000万像素");
    cancelCrop();
    width.value = image.naturalWidth;
    height.value = image.naturalHeight;
    cx.value = width.value / 2;
    cy.value = height.value / 2;
    zoom.value = 1;
    file.value = selected;
    imageUrl.value = url;
  } catch (e: any) {
    URL.revokeObjectURL(url);
    avatarError.value = e.message || "图片无法读取，请重新选择";
  }
}
const points = new Map<number, { x: number; y: number }>();
function down(e: PointerEvent) {
  if (busy.value) return;
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  points.set(e.pointerId, { x: e.clientX, y: e.clientY });
}
function move(e: PointerEvent) {
  const previous = points.get(e.pointerId);
  if (!previous || busy.value) return;
  const old = [...points.values()];
  points.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const now = [...points.values()];
  if (now.length === 2) {
    const distance = (a: typeof now) =>
      Math.hypot(a[0].x - a[1].x, a[0].y - a[1].y);
    const d = distance(old);
    if (d > 0)
      zoom.value = Math.max(1, Math.min(4, (zoom.value * distance(now)) / d));
  } else {
    cx.value -= ((e.clientX - previous.x) * cropSize.value) / 240;
    cy.value -= ((e.clientY - previous.y) * cropSize.value) / 240;
  }
  clamp();
}
async function saveName() {
  if (busy.value) return;
  nameError.value = "";
  busy.value = true;
  try {
    store.user = await api("/api/v1/auth/me", {
      method: "PATCH",
      body: { username: username.value },
    });
    editing.value = false;
    notify("用户名已保存");
  } catch (e: any) {
    nameError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function saveAvatar() {
  if (busy.value || !file.value) return;
  busy.value = true;
  avatarError.value = "";
  const form = new FormData();
  form.append("file", file.value);
  form.append("x", String(cx.value - cropSize.value / 2));
  form.append("y", String(cy.value - cropSize.value / 2));
  form.append("size", String(cropSize.value));
  try {
    store.user = await api("/api/v1/auth/me/avatar", {
      method: "PUT",
      body: form,
    });
    cancelCrop();
    notify("头像已保存");
  } catch (e: any) {
    avatarError.value = e.message;
  } finally {
    busy.value = false;
  }
}
async function reset() {
  busy.value = true;
  avatarError.value = "";
  try {
    store.user = await api("/api/v1/auth/me/avatar", { method: "DELETE" });
    notify("已恢复默认头像");
  } catch (e: any) {
    avatarError.value = e.message;
  } finally {
    busy.value = false;
  }
}
onUnmounted(() => {
  clearTimeout(timer);
  cancelCrop();
});
</script>
<template>
  <div class="profile-editor">
    <template v-if="!imageUrl">
      <div v-if="!editing" class="profile-name">
        <span>用户名：{{ store.user?.username }}</span
        ><button
          class="bare-pencil"
          aria-label="修改用户名"
          :disabled="busy"
          @click="
            username = store.user.username;
            editing = true;
            nameError = '';
          "
        >
          <Icon name="pencil" :size="18" />
        </button>
      </div>
      <form v-else @submit.prevent="saveName">
        <label
          >用户名<input
            v-model="username"
            maxlength="100"
            required
            :disabled="busy"
        /></label>
        <p class="muted small">修改后请使用新用户名登录</p>
        <p v-if="nameError" class="error" role="alert">{{ nameError }}</p>
        <div class="profile-actions">
          <button class="primary" :disabled="busy">保存</button
          ><button type="button" :disabled="busy" @click="editing = false">
            取消
          </button>
        </div>
      </form>
      <div class="profile-avatar-row">
        <button
          class="avatar-picker"
          aria-label="上传头像"
          :disabled="busy"
          @click="picker?.click()"
        >
          <Avatar :url="store.user?.avatar_url" /></button
        ><button
          class="bare-pencil"
          aria-label="修改头像"
          :disabled="busy"
          @click="picker?.click()"
        >
          <Icon name="pencil" :size="18" />
        </button>
      </div>
      <button
        v-if="store.user?.avatar_url"
        class="text-button small"
        :disabled="busy"
        @click="reset"
      >
        恢复默认头像
      </button>
    </template>
    <template v-else>
      <p>调整头像</p>
      <p class="muted small">拖动图片调整位置，双指或滑杆缩放</p>
      <div
        class="avatar-crop"
        aria-label="头像裁剪预览"
        @pointerdown="down"
        @pointermove="move"
        @pointerup="points.delete($event.pointerId)"
        @pointercancel="points.delete($event.pointerId)"
      >
        <img
          :src="imageUrl"
          :style="previewStyle"
          alt="裁剪预览"
          draggable="false"
        />
      </div>
      <label
        >缩放<input
          v-model.number="zoom"
          type="range"
          min="1"
          max="4"
          step="0.01"
          :disabled="busy"
          @input="clamp"
      /></label>
      <div class="profile-actions">
        <button class="primary" :disabled="busy" @click="saveAvatar">
          {{ busy ? "正在保存…" : "保存头像" }}</button
        ><button :disabled="busy" @click="cancelCrop">取消</button>
      </div>
    </template>
    <input
      ref="picker"
      type="file"
      accept="image/jpeg,image/png,image/webp"
      hidden
      @change="choose"
    />
    <p v-if="avatarError" class="error" role="alert">{{ avatarError }}</p>
    <p v-if="success" class="muted small" role="status">{{ success }}</p>
  </div>
</template>
