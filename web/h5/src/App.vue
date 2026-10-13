<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import BottomNav from "./components/BottomNav.vue";
const route = useRoute();
const hasNavigation = computed(
  () =>
    ["/", "/practice", "/notebook", "/recalls"].includes(route.path) ||
    route.path.startsWith("/recalls/") ||
    route.path.startsWith("/recall-session/"),
);
</script>
<template>
  <main class="app-shell" :class="{ 'with-bottom-nav': hasNavigation }">
    <RouterView />
    <BottomNav v-if="hasNavigation" />
  </main>
</template>
