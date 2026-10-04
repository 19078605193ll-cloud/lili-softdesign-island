import confetti from "canvas-confetti";
import type { StreakMilestone } from "./composables/useAnswerStreak";

const celebrationColors = [
  "#FF3B30", // Red
  "#FF9500", // Orange
  "#FFD700", // Gold
  "#34C759", // Green
  "#007AFF", // Blue
  "#AF52DE", // Purple
  "#FF2D55", // Pink
];

export function playCelebration(
  canvas: HTMLCanvasElement,
  level: StreakMilestone,
): () => void {
  let instance: ReturnType<typeof confetti.create> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => {
    clearTimeout(timer);
    try {
      instance?.reset();
    } catch {
      /* Cosmetic failure only. */
    }
    canvas.hidden = true;
  };
  try {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return stop;
    instance = confetti.create(canvas, {
      resize: true,
      disableForReducedMotion: true,
    });
    canvas.hidden = false;
    const fire = (options: confetti.Options) => {
      instance!({
        colors: celebrationColors,
        gravity: 1.15,
        ticks: 110,
        ...options,
      })?.catch(stop);
    };
    if (level === 3) {
      fire({
        particleCount: 35,
        origin: { x: 0.5, y: 0.85 },
        spread: 55,
        startVelocity: 24,
        scalar: 0.75,
      });
    } else {
      const burst = (count: number, x: number, angle: number) => {
        const base = { origin: { x, y: 0.85 }, angle };
        // Realistic Look: mix narrow fast particles with wider, slower layers.
        for (const [ratio, spread, startVelocity, decay, scalar] of [
          [0.25, 26, 45, 0.9, 0.85],
          [0.2, 60, 36, 0.9, 0.85],
          [0.35, 100, 30, 0.91, 0.7],
          [0.1, 120, 22, 0.92, 1],
          [0.1, 120, 36, 0.9, 0.85],
        ])
          fire({
            ...base,
            particleCount: Math.round(count * ratio),
            spread,
            startVelocity,
            decay,
            scalar,
          });
      };
      if (level === 5) burst(100, 0.5, 90);
      else {
        burst(80, 0.18, 65);
        burst(80, 0.82, 115);
      }
    }
    timer = setTimeout(stop, { 3: 1200, 5: 1800, 10: 2200 }[level]);
  } catch {
    stop();
  }
  return stop;
}
