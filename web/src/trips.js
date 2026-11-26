// Helpers for the sidebar's "By Trip" tree and the trip view. Trips come
// from the API (trips.py) already built; this only groups and orders them.

// "trip" (two or more calendar days) or "outing" (one day).
export function visibleTrips(trips, showOutings) {
  return showOutings ? trips : trips.filter((trip) => trip.kind !== "outing");
}

// The calendar year a trip started in. `start_at` is wall-clock time with no
// zone, so the year is read straight off the string -- converting it
// through Date would shift a late-night photo into another day or year.
export function tripYear(trip) {
  const match = /^(\d{4})/.exec(trip.start_at ?? "");
  return match ? Number(match[1]) : null;
}

// [{year, trips: [...], count}], newest year first, newest trip first within
// it. Trips with no dates go last under a null year. `count` is the photos
// in that year's trips, shown beside the year in the tree.
export function groupTripsByYear(trips) {
  const byYear = new Map();
  for (const trip of trips) {
    const year = tripYear(trip);
    if (!byYear.has(year)) byYear.set(year, []);
    byYear.get(year).push(trip);
  }
  const startOf = (trip) => trip.start_at ?? "";
  return [...byYear.entries()]
    .sort(([a], [b]) => (a === null) - (b === null) || (b ?? 0) - (a ?? 0))
    .map(([year, list]) => ({
      year,
      trips: [...list].sort((a, b) => startOf(b).localeCompare(startOf(a))),
      count: list.reduce((sum, trip) => sum + trip.photo_count, 0),
    }));
}

// The photos in `ids`' order (a trip's own chronological order), skipping
// ids not in the loaded list.
export function orderByIds(photos, ids) {
  const byId = new Map(photos.map((photo) => [photo.id, photo]));
  return ids.filter((id) => byId.has(id)).map((id) => byId.get(id));
}

// "May 10 – 12, 2019" style range from the trip's own wall-clock strings.
export function formatTripRange(trip) {
  const parse = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value ?? "");
    return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
  };
  const start = parse(trip.start_at);
  const end = parse(trip.end_at);
  if (!start) return "";
  const month = (date) => date.toLocaleDateString(undefined, { month: "short" });
  if (!end || start.getTime() === end.getTime()) {
    return `${month(start)} ${start.getDate()}, ${start.getFullYear()}`;
  }
  if (start.getFullYear() === end.getFullYear() && start.getMonth() === end.getMonth()) {
    return `${month(start)} ${start.getDate()} \u2013 ${end.getDate()}, ${start.getFullYear()}`;
  }
  if (start.getFullYear() === end.getFullYear()) {
    return `${month(start)} ${start.getDate()} \u2013 ${month(end)} ${end.getDate()}, ${start.getFullYear()}`;
  }
  return (
    `${month(start)} ${start.getDate()}, ${start.getFullYear()} \u2013 ` +
    `${month(end)} ${end.getDate()}, ${end.getFullYear()}`
  );
}
