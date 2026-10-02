<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "../api";
import { useLearner } from "../store";

const email = ref(""), username = ref(""), password = ref(""), confirmPassword = ref("");
const error = ref(""), busy = ref(false);
const router = useRouter(), store = useLearner();

async function register() {
  if (busy.value) return;
  error.value = "";
  if (password.value !== confirmPassword.value) {
    error.value = "两次输入的密码不一致";
    return;
  }
  busy.value = true;
  try {
    const result = await api("/api/v1/auth/register", {
      method: "POST",
      body: { email: email.value, username: username.value, password: password.value, confirm_password: confirmPassword.value },
    });
    password.value = confirmPassword.value = "";
    if (!result.authenticated) {
      await router.replace({ path: "/login", query: { registered: "1" } });
      return;
    }
    try {
      await store.boot();
      await router.replace("/");
    } catch {
      await router.replace({ path: "/login", query: { registered: "1" } });
    }
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="login-page">
    <p class="verse">从今天开始，<br />把每一步学扎实。</p>
    <h1>软设岛</h1>
    <p class="muted">软件设计师 · 学习与思考</p>
    <form class="card login-form" @submit.prevent="register">
      <h2>创建学习账号</h2>
      <label>邮箱<input v-model="email" type="email" autocomplete="email" maxlength="254" required :disabled="busy" /></label>
      <label>用户名<input v-model="username" autocomplete="username" maxlength="100" required :disabled="busy" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="new-password" minlength="12" maxlength="128" required :disabled="busy" /></label>
      <p class="muted small">密码需为 12—128 位</p>
      <label>确认密码<input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="12" maxlength="128" required :disabled="busy" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" :disabled="busy">{{ busy ? "正在注册…" : "注册并开始学习" }}<span>→</span></button>
      <p class="muted small">已有账号？<RouterLink to="/login">返回登录</RouterLink></p>
    </form>
  </section>
</template>
