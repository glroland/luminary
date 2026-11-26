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
