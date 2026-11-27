import { describe, expect, it } from "vitest";
import { canRotate, normalizeRotation, rotateBox, rotatedBy, rotatedSize } from "./rotation.js";

describe("rotation", () => {
  it("normalizes any sign into 0-359", () => {
    expect(normalizeRotation(-45)).toBe(315);
    expect(normalizeRotation(405)).toBe(45);
    expect(normalizeRotation(-360)).toBe(0);
    expect(rotatedBy(315, 45)).toBe(0);
    expect(rotatedBy(0, -45)).toBe(315);
    expect(rotatedBy(undefined, 45)).toBe(45);
  });

  it("swaps the sides for a quarter turn and grows the canvas at 45", () => {
    expect(rotatedSize(1000, 800, 90)).toEqual({ width: expect.closeTo(800), height: expect.closeTo(1000) });
    const s = rotatedSize(100, 100, 45);
    expect(s.width).toBeCloseTo(141.42, 1);
    expect(s.height).toBeCloseTo(141.42, 1);
  });

  it("moves a box clockwise: top-left of a 100x50 photo ends up top-right after 90", () => {
    const box = rotateBox({ x1: 0, y1: 0, x2: 10, y2: 10 }, 100, 50, 90);
    expect(box.width).toBeCloseTo(50);
    expect(box.height).toBeCloseTo(100);
    expect(box.x1).toBeCloseTo(40);
    expect(box.x2).toBeCloseTo(50);
    expect(box.y1).toBeCloseTo(0);
    expect(box.y2).toBeCloseTo(10);
  });

  it("is the identity at 0", () => {
    const box = rotateBox({ x1: 5, y1: 6, x2: 20, y2: 30 }, 100, 50, 0);
    expect([box.x1, box.y1, box.x2, box.y2]).toEqual([5, 6, 20, 30]);
  });

  it("only rotates stills", () => {
    expect(canRotate({ media_kind: "image" })).toBe(true);
    expect(canRotate({})).toBe(true);
    expect(canRotate({ media_kind: "video" })).toBe(false);
    expect(canRotate({ media_kind: "animated" })).toBe(false);
  });
});
