import { defineStore } from "pinia";
import { api, resetAuth } from "./api";
export const useLearner = defineStore("learner", {
  state: () => ({
    user: null as any,
    subjects: [] as any[],
    subjectId: "",
    motto: "",
    ready: false,
  }),
  getters: { subject: (s) => s.subjects.find((x) => x.id === s.subjectId) },
  actions: {
    async boot() {
      this.user = await api("/api/v1/auth/me");
      const [s, p] = await Promise.all([
        api("/practice/subjects"),
        api("/learning/preferences"),
      ]);
      this.subjects = s.items;
      this.subjectId = p.subject_id || s.items[0]?.id || "";
      this.motto = p.motto;
      this.ready = true;
    },
    async choose(id: string) {
      await api("/learning/preferences", {
        method: "PATCH",
        body: { subject_id: id },
      });
      this.subjectId = id;
    },
    async logout() {
      await api("/api/v1/auth/logout", { method: "POST" });
      this.clear();
    },
    clear() {
      this.$reset();
      resetAuth();
    },
  },
});
