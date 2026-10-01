import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests", testMatch: "controlled-smoke.ts", workers: 1, retries: 0,
  timeout: 120000, outputDir: "test-results-controlled", reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:8001", browserName: "chromium",  headless: true, trace: "off", screenshot: "off" },
});
