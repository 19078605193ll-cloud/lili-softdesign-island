<script setup lang="ts">
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { api } from "../api";
import { useLearner } from "../store";
const username = ref(""),
  password = ref(""),
  error = ref(""),
  busy = ref(false);
const router = useRouter(),
  route = useRoute(),
  store = useLearner();
async function login() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await api("/api/v1/auth/login", {
      method: "POST",
      body: { username: username.value, password: password.value },
    });
    await store.boot();
    const next = String(route.query.next || "/");
    await router.replace(
      next.startsWith("/") && !next.startsWith("//") ? next : "/",
    );
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="login-page">
    <p class="verse">专注每一次练习，<br />让进步看得见。</p>
    <h1>软设岛</h1>
    <p class="muted">软件设计师 · 学习与思考</p>
    <form class="card login-form" @submit.prevent="login">
      <h2>欢迎回来</h2>
      <p v-if="route.query.registered" class="muted small" role="status">注册成功，请登录后继续学习。</p>
      <label
        >用户名或邮箱<input
          v-model="username"
          autocomplete="username"
          maxlength="254"
          required /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          minlength="12"
          maxlength="128"
          required
      /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy">
        {{ busy ? "正在登录…" : "登录，继续学习" }}<span>→</span>
      </button>
      <p class="muted small">没有账号？<RouterLink to="/register">立即注册</RouterLink></p>
    </form>
  </section>
</template>
