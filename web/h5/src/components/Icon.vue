<script setup lang="ts">
import {
  Home,
  Pencil,
  BookOpen,
  Bookmark,
  UserRound,
  ChevronLeft,
  ChevronRight,
  ArrowRight,
  Search,
  CalendarDays,
  SlidersHorizontal,
  Star,
  Check,
  X,
  Lightbulb,
  Send,
  RefreshCw,
  LogOut,
  Brain,
  Plus,
  MessageCircle,
  Mic,
  Square,
} from "lucide-vue-next";
defineProps<{ name: string; size?: number }>();
const icons: Record<string, any> = {
  home: Home,
  pencil: Pencil,
  book: BookOpen,
  bookmark: Bookmark,
  user: UserRound,
  left: ChevronLeft,
  right: ChevronRight,
  arrow: ArrowRight,
  search: Search,
  calendar: CalendarDays,
  edit: SlidersHorizontal,
  star: Star,
  check: Check,
  x: X,
  bulb: Lightbulb,
  send: Send,
  refresh: RefreshCw,
  logout: LogOut,
  brain: Brain,
  plus: Plus,
  chat: MessageCircle,
  mic: Mic,
  stop: Square,
};
</script>
<template>
  <component
    :is="icons[name] || BookOpen"
    :size="size || 22"
    :stroke-width="1.8"
    aria-hidden="true"
  />
</template>
