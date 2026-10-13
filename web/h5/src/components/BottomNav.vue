<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import Icon from "./Icon.vue";
const route = useRoute();
const isHomeContext = computed(
  () =>
    route.path === "/recalls" ||
    route.path.startsWith("/recalls/") ||
    route.path.startsWith("/recall-session/"),
);
</script>
<template>
  <nav class="bottom-nav" aria-label="主导航">
    <RouterLink
      to="/"
      :class="{ 'context-active': isHomeContext }"
      :aria-current="isHomeContext ? 'page' : undefined"
      ><Icon name="home" /><span>首页</span></RouterLink
    ><RouterLink to="/practice"
      ><Icon name="book" /><span>刷题</span></RouterLink
    ><RouterLink to="/notebook"
      ><Icon name="bookmark" /><span>错题本</span></RouterLink
    >
  </nav>
</template>
