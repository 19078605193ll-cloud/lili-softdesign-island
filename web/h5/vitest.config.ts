import { defineConfig } from "vitest/config";
export default defineConfig({
  server: { fs: { allow: [".", "../../vendor/lili-voice-input/packages/browser"] } },
  test: {
    include: [
      "src/**/*.test.ts",
      "../../vendor/lili-voice-input/packages/browser/tests/**/*.test.ts",
    ],
  },
});
