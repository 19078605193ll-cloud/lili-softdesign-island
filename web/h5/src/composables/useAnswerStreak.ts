export type StreakMilestone = 3 | 5 | 10;
type State = { count: number; attempts: string[] };
type StorageAccess = () => Pick<Storage, "getItem" | "setItem">;

// Shared fallback survives route unmounts when browser storage is unavailable.
export function createStreakRecorder(storage: StorageAccess) {
  const memory = new Map<string, State>();
  const unavailable = new Set<string>();
  return (
    userId: string,
    sessionId: string,
    attemptId: string,
    correct: boolean,
  ): StreakMilestone | null => {
    if (!userId || !sessionId || !attemptId) return null;
    const key = "answer-streak:v1:" + JSON.stringify([userId, sessionId]);
    let state = memory.get(key) || { count: 0, attempts: [] };
    try {
      if (unavailable.has(key)) throw new Error("Use memory fallback");
      const raw = storage().getItem(key);
      const saved = raw ? JSON.parse(raw) : null;
      if (
        saved &&
        Number.isSafeInteger(saved.count) &&
        saved.count >= 0 &&
        Array.isArray(saved.attempts) &&
        saved.attempts.every((id: unknown) => typeof id === "string")
      ) {
        state = saved;
      } else if (!raw) state = { count: 0, attempts: [] };
    } catch {
      /* Storage failure must never interrupt answering. */
    }
    if (state.attempts.includes(attemptId)) return null;
    state = {
      count: correct ? state.count + 1 : 0,
      attempts: [...state.attempts, attemptId],
    };
    memory.set(key, state);
    try {
      storage().setItem(key, JSON.stringify(state));
      unavailable.delete(key);
    } catch {
      unavailable.add(key);
    }
    return state.count === 3 || state.count === 5 || state.count === 10
      ? state.count
      : null;
  };
}

const record = createStreakRecorder(() => sessionStorage);
export function useAnswerStreak() {
  return { record };
}
