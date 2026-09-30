import { onMounted, onUnmounted, ref } from "vue";

// visualViewport follows the software keyboard on iOS as well as Android.
export function useVisualViewport() {
  const style = ref<Record<string, string>>({});
  function update() {
    const viewport = window.visualViewport;
    style.value = {
      "--viewport-height": `${viewport?.height || window.innerHeight}px`,
      "--viewport-top": `${viewport?.offsetTop || 0}px`,
    };
  }
  onMounted(() => {
    update();
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
  });
  onUnmounted(() => {
    window.visualViewport?.removeEventListener("resize", update);
    window.visualViewport?.removeEventListener("scroll", update);
    window.removeEventListener("resize", update);
  });
  return style;
}
