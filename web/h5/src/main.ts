import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import { useLearner } from "./store";
import "./styles/tokens.css";
import "./styles/main.css";
const router = createRouter({
  history: createWebHistory("/h5/"),
  routes: [
    { path: "/", component: () => import("./pages/Home.vue") },
    { path: "/login", component: () => import("./pages/Login.vue") },
    { path: "/practice", component: () => import("./pages/Practice.vue") },
    { path: "/session/:id", component: () => import("./pages/Session.vue") },
    { path: "/related/:id", component: () => import("./pages/Related.vue") },
    { path: "/notebook", component: () => import("./pages/Notebook.vue") },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
  scrollBehavior(to, from, saved) {
    return (
      saved || {
        top:
          to.path === "/notebook"
            ? Number(sessionStorage.getItem("notebook-scroll") || 0)
            : 0,
      }
    );
  },
});
const app = createApp(App);
app.use(createPinia());
app.use(router);
router.beforeEach(async (to) => {
  if (to.path === "/login") return;
  const store = useLearner();
  if (!store.ready) {
    try {
      await store.boot();
    } catch {
      return { path: "/login", query: { next: to.fullPath } };
    }
  }
});
window.addEventListener("session-expired", () => {
  useLearner().clear();
  router.replace({
    path: "/login",
    query: { next: router.currentRoute.value.fullPath },
  });
});
app.mount("#app");
