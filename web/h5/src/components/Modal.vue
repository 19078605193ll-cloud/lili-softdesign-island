<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
defineProps<{ title: string }>();
const emit = defineEmits(["close"]);
const root = ref<HTMLDialogElement>();
onMounted(() => root.value?.showModal());
onUnmounted(() => root.value?.close());
</script>
<template>
  <dialog ref="root" class="modal" @cancel.prevent="emit('close')">
    <header>
      <h2>{{ title }}</h2>
      <button class="icon-button" aria-label="关闭" @click="emit('close')">
        ×
      </button>
    </header>
    <slot />
  </dialog>
</template>
