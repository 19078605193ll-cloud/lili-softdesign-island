<script setup lang="ts">
import { computed } from "vue";
import type {
  RecallContent,
  RecallBlank,
  RecallTableCell,
  RecallToken,
} from "../recall";
type RecallSlotOutcome = "correct" | "incorrect";
type RecallSlotPresentation = {
  value: string;
  outcome: RecallSlotOutcome;
};
const props = defineProps<{
  content: RecallContent;
  blanks: RecallBlank[];
  full?: boolean;
  currentKey?: string | null;
  slotPresentations?: Record<string, RecallSlotPresentation>;
}>();
const map = computed(() =>
  Object.fromEntries(
    props.blanks.map((b, i) => [b.key, { ...b, number: i + 1 }]),
  ),
);
function label(t: RecallToken) {
  const presentation = t.blank_key
    ? props.slotPresentations?.[t.blank_key]
    : undefined;
  if (presentation)
    return `${presentation.outcome === "correct" ? "✓" : "×"} ${presentation.value}`;
  return t.blank_key
    ? props.full
      ? map.value[t.blank_key]?.answer || "—"
      : `（${map.value[t.blank_key]?.number || "?"}）____`
    : t.text || "";
}
function outcome(t: RecallToken) {
  return t.blank_key
    ? props.slotPresentations?.[t.blank_key]?.outcome
    : undefined;
}
function answerLabel(t: RecallToken) {
  const presentation = t.blank_key
    ? props.slotPresentations?.[t.blank_key]
    : undefined;
  if (!presentation) return undefined;
  return `你的答案：${presentation.value}，${
    presentation.outcome === "correct" ? "答对" : "未答对"
  }`;
}
function cellTokens(cell: RecallTableCell) {
  return Array.isArray(cell) ? cell : [cell];
}
const graph = computed(() =>
  props.content.type === "relation" ? props.content : null,
);
const width = computed(() =>
  Math.max(720, ...(graph.value?.nodes.map((n) => n.x + 140) || [])),
);
const height = computed(() =>
  Math.max(180, ...(graph.value?.nodes.map((n) => n.y + 65) || [])),
);
const edges = computed(
  () =>
    graph.value?.edges.map((e) => {
      const from = graph.value!.nodes.find((n) => n.id === e.source)!;
      const to = graph.value!.nodes.find((n) => n.id === e.target)!;
      const dx = to.x - from.x,
        dy = to.y - from.y;
      const inset = Math.min(
        Math.abs(dx) ? 102 / Math.abs(dx) : Infinity,
        Math.abs(dy) ? 26 / Math.abs(dy) : Infinity,
        0.45,
      );
      return {
        ...e,
        from,
        to,
        x1: from.x + dx * inset,
        y1: from.y + dy * inset,
        x2: to.x - dx * inset,
        y2: to.y - dy * inset,
      };
    }) || [],
);
</script>
<template>
  <div class="recall-content">
    <div
      v-if="content.type === 'table'"
      class="recall-scroll"
      tabindex="0"
      aria-label="知识对比表，可横向滚动"
    >
      <table>
        <thead>
          <tr>
            <th v-for="(col, i) in content.columns" :key="i" scope="col">
              {{ col }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in content.rows" :key="i">
            <td v-for="(cell, j) in row" :key="j">
              <span
                v-for="(t, k) in cellTokens(cell)"
                :key="t.blank_key || `${k}-${t.text || ''}`"
                class="recall-cell-token"
                :class="{
                  'recall-current': !!t.blank_key && t.blank_key === currentKey,
                  'recall-slot': t.blank_key,
                  'recall-slot-result': outcome(t),
                  'recall-slot-correct': outcome(t) === 'correct',
                  'recall-slot-incorrect': outcome(t) === 'incorrect',
                }"
                :aria-label="answerLabel(t)"
                :title="answerLabel(t)"
                >{{ label(t) }}</span
              >
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="content.type === 'sentence'" class="recall-sentences">
      <p v-for="(p, i) in content.paragraphs" :key="i">
        <span class="recall-number">{{ String(i + 1).padStart(2, "0") }}</span
        ><span
          ><span
            v-for="(t, j) in p"
            :key="j"
            :class="{
              'recall-current': !!t.blank_key && t.blank_key === currentKey,
              'recall-slot': t.blank_key,
              'recall-slot-result': outcome(t),
              'recall-slot-correct': outcome(t) === 'correct',
              'recall-slot-incorrect': outcome(t) === 'incorrect',
            }"
            :aria-label="answerLabel(t)"
            :title="answerLabel(t)"
            >{{ label(t) }}</span
          ></span
        >
      </p>
    </div>
    <template v-else-if="graph">
      <div
        class="recall-scroll"
        tabindex="0"
        aria-label="关系推演图，可横向滚动"
      >
        <svg
          class="recall-graph"
          :viewBox="`0 0 ${width} ${height}`"
          role="img"
          aria-label="前提经过规则推导得到结论"
        >
          <defs>
            <marker
              id="recall-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          <g v-for="(e, i) in edges" :key="i">
            <line
              :x1="e.x1"
              :y1="e.y1"
              :x2="e.x2"
              :y2="e.y2"
              marker-end="url(#recall-arrow)"
            />
            <text
              :x="(e.from.x + e.to.x) / 2"
              :y="(e.from.y + e.to.y) / 2 - 20"
              :class="{
                'active-label':
                  !!e.label.blank_key && e.label.blank_key === currentKey,
                'recall-graph-correct': outcome(e.label) === 'correct',
                'recall-graph-incorrect': outcome(e.label) === 'incorrect',
              }"
              :aria-label="answerLabel(e.label)"
            >
              {{ label(e.label) }}
            </text>
          </g>
          <g v-for="n in graph.nodes" :key="n.id">
            <rect
              :x="n.x - 100"
              :y="n.y - 24"
              width="200"
              height="48"
              rx="12"
              :class="{
                'active-node':
                  !!n.label.blank_key && n.label.blank_key === currentKey,
                'recall-result-correct-node': outcome(n.label) === 'correct',
                'recall-result-incorrect-node':
                  outcome(n.label) === 'incorrect',
              }"
            />
            <text
              :x="n.x"
              :y="n.y + 6"
              :class="{
                'recall-graph-correct': outcome(n.label) === 'correct',
                'recall-graph-incorrect': outcome(n.label) === 'incorrect',
              }"
              :aria-label="answerLabel(n.label)"
            >
              {{ label(n.label) }}
            </text>
          </g>
        </svg>
      </div>
      <ol class="recall-steps">
        <li v-for="(p, i) in graph.steps" :key="i">
          <span
            v-for="(t, j) in p"
            :key="j"
            :class="{
              'recall-current': !!t.blank_key && t.blank_key === currentKey,
              'recall-slot': t.blank_key,
              'recall-slot-result': outcome(t),
              'recall-slot-correct': outcome(t) === 'correct',
              'recall-slot-incorrect': outcome(t) === 'incorrect',
            }"
            :aria-label="answerLabel(t)"
            :title="answerLabel(t)"
            >{{ label(t) }}</span
          >
        </li>
      </ol>
    </template>
    <p v-if="content.note" class="recall-note">{{ content.note }}</p>
  </div>
</template>
