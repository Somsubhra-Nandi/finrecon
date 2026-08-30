import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("api", () => {
  afterEach(() => vi.restoreAllMocks());

  it("turns an unexpected successful HTML response into a useful API error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<!doctype html><title>Vite</title>", { status: 200, headers: { "Content-Type": "text/html" } })));

    await expect(api("/api/benchmarks")).rejects.toMatchObject({
      code: "invalid_api_response",
      status: 200,
    });
  });
});
