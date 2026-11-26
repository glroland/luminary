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
