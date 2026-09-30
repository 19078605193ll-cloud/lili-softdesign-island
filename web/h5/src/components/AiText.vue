<script setup lang="ts">
import { watch, ref, nextTick } from "vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { diagramSource } from "../diagram";
const props = defineProps<{ text: string }>();
const root = ref<HTMLElement>();
const error = ref(false);
let revision = 0;
const markdown = new MarkdownIt({ html: false, linkify: false });
const html = ref("");
async function render() {
  const current = ++revision;
  error.value = false;
  html.value = DOMPurify.sanitize(markdown.render(props.text), {
    FORBID_TAGS: ["img", "iframe", "script", "style"],
  });
  await nextTick();
  if (current !== revision) return;
  const blocks = root.value?.querySelectorAll("code.language-mermaid") || [];
  if (!blocks.length) return;
  try {
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      suppressErrorRendering: true,
      maxTextSize: 10000,
      htmlLabels: false,
      flowchart: { htmlLabels: false },
    });
    for (const block of blocks) {
      const source = diagramSource(block.textContent || "");
      const { svg } = await mermaid.render(
        "diagram-" + crypto.randomUUID().replaceAll("-", ""),
        source,
      );
      if (current !== revision) return;
      const figure = document.createElement("div");
      figure.className = "diagram";
      figure.innerHTML = DOMPurify.sanitize(svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
      });
      block.parentElement?.replaceWith(figure);
    }
  } catch {
    error.value = true;
  }
}
watch(() => props.text, render, { immediate: true });
</script>
<template>
  <div ref="root" class="rich-text ai-text" v-html="html"></div>
  <button v-if="error" class="text-button" @click="render">
    结构图暂未绘制，已保留步骤文本 · 重试
  </button>
</template>
