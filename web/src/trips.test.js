import { describe, expect, it } from "vitest";
import { formatTripRange, groupTripsByYear, orderByIds, tripYear, visibleTrips } from "./trips.js";

const trip = (id, start_at, extra = {}) => ({ id, name: id, kind: "trip", start_at, end_at: start_at, photo_count: 10, ...extra });

describe("trips.js", () => {
  it("hides day outings unless asked", () => {
    const list = [trip("a", "2019-05-10T09:00:00"), trip("b", "2019-06-01T09:00:00", { kind: "outing" })];
    expect(visibleTrips(list, false).map((t) => t.id)).toEqual(["a"]);
    expect(visibleTrips(list, true).map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("reads the year off the wall-clock string, not through the browser's timezone", () => {
    expect(tripYear(trip("a", "2019-12-31T23:30:00"))).toBe(2019);
    expect(tripYear({ start_at: null })).toBeNull();
  });

  it("groups by year, newest first, undated last", () => {
    const groups = groupTripsByYear([
      trip("old", "2018-03-01T09:00:00", { photo_count: 5 }),
      trip("undated", null),
      trip("new-early", "2021-02-01T09:00:00", { photo_count: 7 }),
      trip("new-late", "2021-11-01T09:00:00", { photo_count: 3 }),
    ]);
    expect(groups.map((g) => g.year)).toEqual([2021, 2018, null]);
    expect(groups[0].trips.map((t) => t.id)).toEqual(["new-late", "new-early"]);
    expect(groups[0].count).toBe(10);
  });

  it("orders photos by the trip's ids and skips unknown ones", () => {
    const photos = [{ id: "1" }, { id: "2" }, { id: "3" }];
    expect(orderByIds(photos, ["3", "x", "1"]).map((p) => p.id)).toEqual(["3", "1"]);
  });

  it("formats ranges", () => {
    const range = (start_at, end_at) => formatTripRange({ start_at, end_at });
    expect(range("2019-05-10T09:00:00", "2019-05-10T17:00:00")).toMatch(/May 10, 2019/);
    expect(range("2019-05-10T09:00:00", "2019-05-12T17:00:00")).toMatch(/May 10 – 12, 2019/);
    expect(range("2019-05-30T09:00:00", "2019-06-02T17:00:00")).toMatch(/May 30 – Jun 2, 2019/);
    expect(range("2019-12-30T09:00:00", "2020-01-02T17:00:00")).toMatch(/Dec 30, 2019 – Jan 2, 2020/);
    expect(formatTripRange({ start_at: null, end_at: null })).toBe("");
  });
});
