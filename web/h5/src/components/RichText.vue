<script setup lang="ts">
import { ref } from "vue";
defineProps<{ html: string }>();
const zoom = ref("");
const failed = ref<HTMLImageElement[]>([]);
function click(e: MouseEvent) {
  if (e.target instanceof HTMLImageElement) zoom.value = e.target.src;
}
function imageError(e: Event) {
  if (e.target instanceof HTMLImageElement) failed.value.push(e.target);
}
function retry() {
  for (const image of failed.value) {
    const source = image.src;
    image.src = "";
    image.src = source;
  }
  failed.value = [];
}
</script>
<template>
  <div
    class="rich-text"
    @click="click"
    @error.capture="imageError"
    v-html="html"
  ></div>
  <button v-if="failed.length" class="text-button" @click="retry">
    图片加载失败，点击重试
  </button>
  <div
    v-if="zoom"
    class="image-overlay"
    role="dialog"
    aria-modal="true"
    aria-label="题图放大"
    @click="zoom = ''"
    @keydown.esc="zoom = ''"
    tabindex="0"
  >
    <button @click="zoom = ''">关闭图片</button
    ><img :src="zoom" alt="放大的题目图片" />
  </div>
</template>
