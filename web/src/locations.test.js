import { describe, expect, it } from "vitest";
import { LOCATION_UNKNOWN, buildLocationTree, locationKeyOf, matchesLocation } from "./locations.js";

const us = (state, city) => ({ location_country: "United States", location_state: state, location_city: city });

describe("locations", () => {
  it("roots US photos at the state and other photos at the country", () => {
    const tree = buildLocationTree([
      us("Georgia", "Alpharetta"),
      us("Georgia", "Atlanta"),
      us("North Carolina", "Boone"),
      { location_country: "France", location_state: "Île-de-France", location_city: "Paris" },
      { location_name: "Somewhere, old row" },
      {},
    ]);
    expect(Object.keys(tree.children).sort()).toEqual(["France", "Georgia", "North Carolina"]);
    expect(tree.children.Georgia.count).toBe(2);
    expect(tree.children.France.children["Île-de-France"].children.Paris.count).toBe(1);
    expect(tree.unknown).toBe(2);
  });

  it("matches a node and its descendants, and unknown", () => {
    const photo = us("Georgia", "Alpharetta");
    expect(matchesLocation(photo, "Georgia")).toBe(true);
    expect(matchesLocation(photo, locationKeyOf(photo))).toBe(true);
    expect(matchesLocation(photo, "Geo")).toBe(false);
    expect(matchesLocation(photo, LOCATION_UNKNOWN)).toBe(false);
    expect(matchesLocation({}, LOCATION_UNKNOWN)).toBe(true);
  });
});

import { bubbleSize, clusterPhotos, matchesLocation as matches } from "./locations.js";

describe("map picker helpers", () => {
  const project = (lat, lng) => ({ x: lng * 10, y: lat * 10 });
  const photos = [
    { id: 1, latitude: 1, longitude: 1 },
    { id: 2, latitude: 1.1, longitude: 1.1 },
    { id: 3, latitude: 50, longitude: 50 },
    { id: 4, latitude: null, longitude: null },
  ];

  it("clusters nearby photos and skips unlocated ones", () => {
    const clusters = clusterPhotos(photos, project);
    expect(clusters.map((c) => c.count).sort()).toEqual([1, 2]);
  });

  it("makes busier bubbles larger", () => {
    expect(bubbleSize(100, 100)).toBeGreaterThan(bubbleSize(5, 100));
    expect(bubbleSize(1, 1)).toBe(bubbleSize(1, 50));
  });

  it("matches a map selection by id", () => {
    expect(matches({ id: 2 }, new Set([2]))).toBe(true);
    expect(matches({ id: 3 }, new Set([2]))).toBe(false);
  });
});
