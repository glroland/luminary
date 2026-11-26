// The sidebar's location tree is built from the structured columns the
// geocoder stores beside `location_name` (location_country/_state/_city --
// see geocode.py): country at the root, then state, then city. The United
// States is skipped, so a US photo roots at its state. A photo with no
// country (no GPS, a failed lookup, or one geocoded before these columns
// existed -- "Refresh Locations" fills those in) is "Location Unknown".

export const LOCATION_SEPARATOR = "\u001f";
// Selection value for photos with no location_name.
export const LOCATION_UNKNOWN = "\u001funknown";

// Path segments from root to leaf, or null when the photo has no country.
export function locationPathOf(photo) {
  if (!photo.location_country) return null;
  const parts = photo.location_country === "United States" ? [] : [photo.location_country];
  if (photo.location_state) parts.push(photo.location_state);
  if (photo.location_city) parts.push(photo.location_city);
  return parts.length > 0 ? parts : null;
}

export function locationKeyOf(photo) {
  const path = locationPathOf(photo);
  return path ? path.join(LOCATION_SEPARATOR) : null;
}

export function matchesLocation(photo, selected) {
  if (selected instanceof Set) return selected.has(photo.id);
  const key = locationKeyOf(photo);
  if (selected === LOCATION_UNKNOWN) return key === null;
  return key !== null && (key === selected || key.startsWith(selected + LOCATION_SEPARATOR));
}

// {children: {name: node}, count} tree plus the number of unlocated photos.
export function buildLocationTree(photos) {
  const root = { children: {}, count: 0, unknown: 0 };
  for (const photo of photos) {
    const path = locationPathOf(photo);
    if (!path) {
      root.unknown += 1;
      continue;
    }
    let node = root;
    for (const part of path) {
      node.children[part] ??= { children: {}, count: 0 };
      node = node.children[part];
      node.count += 1;
    }
  }
  return root;
}

// --- Map picker -----------------------------------------------------------

// A selection made on the map is a Set of photo ids (a string key is a
// tree selection); matchesLocation accepts either.
export function hasCoordinates(photo) {
  return photo.latitude != null && photo.longitude != null;
}

// Groups located photos into bubbles. `project(lat, lng)` returns pixel
// coordinates at the map's current zoom, so photos closer than `cellSize`
// pixels merge into one bubble and split apart again as the user zooms in.
export function clusterPhotos(photos, project, cellSize = 60) {
  const cells = new Map();
  for (const photo of photos) {
    if (!hasCoordinates(photo)) continue;
    const { x, y } = project(photo.latitude, photo.longitude);
    const key = `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}`;
    let cell = cells.get(key);
    if (!cell) {
      cell = { key, ids: [], latSum: 0, lngSum: 0 };
      cells.set(key, cell);
    }
    cell.ids.push(photo.id);
    cell.latSum += photo.latitude;
    cell.lngSum += photo.longitude;
  }
  return [...cells.values()].map((cell) => ({
    key: cell.key,
    ids: cell.ids,
    count: cell.ids.length,
    latitude: cell.latSum / cell.ids.length,
    longitude: cell.lngSum / cell.ids.length,
  }));
}

// Bubble diameter in px: area grows with the photo count, so a busy
// location is visibly larger than a quiet one.
export function bubbleSize(count, maxCount) {
  const MIN = 26;
  const MAX = 96;
  if (maxCount <= 1) return MIN;
  return Math.round(MIN + (MAX - MIN) * Math.sqrt((count - 1) / (maxCount - 1)));
}
