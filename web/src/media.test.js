import { describe, expect, it } from "vitest";
import { formatDuration, isMoving, isVideo } from "./media.js";

describe("media", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(42)).toBe("0:42");
    expect(formatDuration(61.6)).toBe("1:02");
    expect(formatDuration(3723)).toBe("1:02:03");
    expect(formatDuration(null)).toBeNull();
    expect(formatDuration(undefined)).toBeNull();
    expect(formatDuration(-1)).toBeNull();
  });

  it("tells videos and moving images apart", () => {
    expect(isVideo({ media_kind: "video" })).toBe(true);
    expect(isVideo({ media_kind: "animated" })).toBe(false);
    expect(isMoving({ media_kind: "video" })).toBe(true);
    expect(isMoving({ media_kind: "animated" })).toBe(true);
    expect(isMoving({ media_kind: "image" })).toBe(false);
    expect(isMoving({})).toBe(false);
  });
});
