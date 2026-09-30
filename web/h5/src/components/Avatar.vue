<script setup lang="ts">
import { ref, watch } from "vue";
import Icon from "./Icon.vue";
const props = defineProps<{ url?: string | null }>();
const broken = ref(false);
watch(
  () => props.url,
  () => (broken.value = false),
);
</script>
<template>
  <span class="user-avatar"
    ><img
      v-if="url && !broken"
      :src="url"
      alt="个人头像"
      @error="broken = true" /><Icon v-else name="user"
  /></span>
</template>
