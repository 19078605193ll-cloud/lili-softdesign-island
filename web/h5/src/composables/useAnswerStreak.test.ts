import { describe, expect, it } from "vitest";
import { createStreakRecorder } from "./useAnswerStreak";

function setup() {
  const data = new Map<string, string>();
  const storage = {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  };
  return { storage, record: createStreakRecorder(() => storage) };
}
describe("answer streak", () => {
  it("celebrates only 3, 5, 10 and restarts after an incorrect answer", () => {
    const { record } = setup();
    expect(
      Array.from({ length: 12 }, (_, i) => record("u", "s", String(i), true)),
    ).toEqual([null, null, 3, null, 5, null, null, null, null, 10, null, null]);
    expect(record("u", "s", "wrong", false)).toBeNull();
    expect([13, 14, 15].map((i) => record("u", "s", String(i), true))).toEqual([
      null,
      null,
      3,
    ]);
  });
  it("deduplicates across restoration and isolates users and sessions", () => {
    const { record, storage } = setup();
    record("u", "s", "1", true);
    record("u", "s", "2", true);
    const restored = createStreakRecorder(() => storage);
    expect(restored("u", "s", "2", false)).toBeNull();
    expect(restored("v", "s", "3", true)).toBeNull();
    expect(restored("u", "t", "3", true)).toBeNull();
    expect(restored("u", "s", "3", true)).toBe(3);
    expect(restored("u", "s", "3", true)).toBeNull();
  });
  it("falls back when access or writes fail, including stale persisted data", () => {
    const inaccessible = createStreakRecorder(() => {
      throw new Error("disabled");
    });
    expect(
      [1, 2, 3].map((i) => inaccessible("u", "s", String(i), true)),
    ).toEqual([null, null, 3]);
    const record = createStreakRecorder(() => ({
      getItem: () => JSON.stringify({ count: 1, attempts: ["1"] }),
      setItem: () => {
        throw new Error("quota");
      },
    }));
    expect(record("u", "s", "2", true)).toBeNull();
    expect(record("u", "s", "3", true)).toBe(3);
  });
});
