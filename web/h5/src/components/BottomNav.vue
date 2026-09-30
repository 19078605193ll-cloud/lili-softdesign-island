<script setup lang="ts">
import Icon from "./Icon.vue";
</script>
<template>
  <nav class="bottom-nav" aria-label="主导航">
    <RouterLink to="/"><Icon name="home" /><span>首页</span></RouterLink
    ><RouterLink to="/practice"
      ><Icon name="book" /><span>刷题</span></RouterLink
    ><RouterLink to="/notebook"
      ><Icon name="bookmark" /><span>错题本</span></RouterLink
    >
  </nav>
</template>
