<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import BottomNav from "./components/BottomNav.vue";
const route = useRoute();
const hasNavigation = computed(() =>
  ["/", "/practice", "/notebook"].includes(route.path),
);
</script>
<template>
  <main class="app-shell" :class="{ 'with-bottom-nav': hasNavigation }">
    <RouterView />
    <BottomNav v-if="hasNavigation" />
  </main>
</template>
