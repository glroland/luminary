const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, options);
  } catch {
    throw new Error("Could not connect to the API. Is it running?");
  }

  if (response.status === 204) {
    return null;
  }

  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    throw new Error(data?.detail || `Could not connect to the API (HTTP ${response.status}).`);
  }

  return data;
}

export async function fetchPhotos() {
  const data = await request("/photos");
  return data.photos;
}

export async function deletePhoto(photoId) {
  await request(`/photos/${photoId}`, { method: "DELETE" });
}

export async function purgeDatabase() {
  await request("/purge", { method: "DELETE" });
}

export async function startSync() {
  return request("/sync", { method: "POST" });
}

export async function fetchAbout() {
  return request("/about");
}

export async function fetchSyncStatus() {
  return request("/sync/status");
}

// Sends one chat message. `photoId` is the photo open in the modal, which
// the model is then shown -- or null for a chat about the library as a
// whole (POST /api/chat).
//
// The server may answer with a normal JSON body, or (when
// LUMINARY_OPENAI_STREAMING is enabled server-side) a streamed
// application/x-ndjson body: one JSON object per line, {"text": ...} for
// each piece of the reply, {"boxes": [...]} when the model located
// something in the photo, {"search_results": [...]} when it searched the
// library. Which one it did is read from the response's own content-type,
// not decided here. Either way this resolves to
// {reply, boxes, searchResults} -- searchResults is the matching photo
// ids, best first, or null if the model didn't search this turn. Pass
// onChunk to also be notified of each reply piece as it arrives (never
// called for a JSON reply).
export async function chatAboutPhoto(photoId, message, history, onChunk, searchList = null, onTool = null) {
  let response;
  try {
    response = await fetch(`${API_BASE}${photoId ? `/photos/${photoId}/chat` : "/chat"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, search_list: searchList }),
    });
  } catch {
    throw new Error("Could not connect to the API. Is it running?");
  }

  const isJson = (response.headers.get("content-type") || "").includes("application/json");

  if (!response.ok) {
    const data = isJson ? await response.json().catch(() => null) : null;
    throw new Error(data?.detail || `Could not connect to the API (HTTP ${response.status}).`);
  }

  if (isJson) {
    const data = await response.json();
    return { reply: data.reply, boxes: data.boxes || [], searchResults: data.search_results ?? null };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  let boxes = [];
  let searchResults = null;
  // A network chunk can end mid-line; keep the unfinished tail for the next.
  let pending = "";

  function handleLine(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (typeof event.text === "string") {
      full += event.text;
      onChunk?.(event.text);
    }
    // {index, name, arguments, status}: sent when the model calls a tool
    // ("running") and again when that call finishes ("done").
    if (event.tool) onTool?.(event.tool);
    if (Array.isArray(event.boxes)) boxes = event.boxes;
    if (Array.isArray(event.search_results)) searchResults = event.search_results;
  }

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    const lines = pending.split("\n");
    pending = lines.pop();
    lines.forEach(handleLine);
  }
  handleLine(pending + decoder.decode());
  return { reply: full, boxes, searchResults };
}

export async function fetchFaces(photoId) {
  const data = await request(`/photos/${photoId}/faces`);
  return data.faces;
}

export async function fetchIndividuals() {
  const data = await request("/individuals");
  return data.individuals;
}

// `kind` is "person" (the default) or "pet": a detected face can only be
// tagged to an individual of its own kind.
export async function createIndividual(name, kind = "person") {
  return request("/individuals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, kind }),
  });
}

export async function tagFace(faceId, personId) {
  return request(`/faces/${faceId}/tag`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_id: personId }),
  });
}

export async function untagFace(faceId) {
  await request(`/faces/${faceId}/tag`, { method: "DELETE" });
}

export async function updateIndividual(personId, name) {
  return request(`/individuals/${personId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteIndividual(personId) {
  await request(`/individuals/${personId}`, { method: "DELETE" });
}

export async function fetchIndividualFaces(personId) {
  const data = await request(`/individuals/${personId}/faces`);
  return data.faces;
}

export async function fetchIndividualRejections(personId) {
  const data = await request(`/individuals/${personId}/rejections`);
  return data.faces;
}

export function rejectionThumbnailUrl(rejectionId) {
  return `${API_BASE}/rejections/${rejectionId}/thumbnail`;
}

export async function deleteRejection(rejectionId) {
  await request(`/rejections/${rejectionId}`, { method: "DELETE" });
}

export function individualThumbnailUrl(personId) {
  return `${API_BASE}/individuals/${personId}/thumbnail`;
}

export function faceThumbnailUrl(faceId) {
  return `${API_BASE}/faces/${faceId}/thumbnail`;
}

export async function refreshFaces(full = false) {
  // full=false (default): non-destructive, match-only. full=true: the
  // destructive redetect-then-match ("Full Face Refresh") -- see
  // recognition.py's module docstring.
  return request(`/faces/refresh?full=${full}`, { method: "POST" });
}

export async function fetchFaceRefreshStatus() {
  return request("/faces/refresh/status");
}

export async function refreshDescriptions() {
  return request("/descriptions/refresh", { method: "POST" });
}

export async function fetchDescriptionRefreshStatus() {
  return request("/descriptions/refresh/status");
}

export async function refreshLocations() {
  return request("/locations/refresh", { method: "POST" });
}

export async function fetchLocationRefreshStatus() {
  return request("/locations/refresh/status");
}

// Trips (trips.py): runs of dated, GPS-tagged photos taken away from home.
// "Rebuild trips" re-runs detection; edits a user has made are kept.
export async function refreshTrips() {
  return request("/trips/refresh", { method: "POST" });
}

export async function fetchTrips() {
  const data = await request("/trips");
  return data.trips;
}

// Trips a user dismissed: hidden, and never recreated by detection.
export async function fetchDismissedTrips() {
  const data = await request("/trips?status=dismissed");
  return data.trips;
}

function json(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// `fields`: any of {name, cover_photo_id, kind}. An empty name or cover goes
// back to the generated/automatic one.
export async function updateTrip(tripId, fields) {
  return request(`/trips/${tripId}`, json("PATCH", fields));
}

// A trip made by hand, from photo ids and/or a date range (YYYY-MM-DD).
export async function createTrip({ name, photoIds = [], startDate = null, endDate = null }) {
  return request("/trips", json("POST", { name, photo_ids: photoIds, start_date: startDate, end_date: endDate }));
}

export async function addTripPhotos(tripId, photoIds) {
  return request(`/trips/${tripId}/photos/add`, json("POST", { photo_ids: photoIds }));
}

export async function removeTripPhotos(tripId, photoIds) {
  return request(`/trips/${tripId}/photos/remove`, json("POST", { photo_ids: photoIds }));
}

// Resolves to the surviving trip (whichever started first).
export async function mergeTrips(tripIds) {
  return request("/trips/merge", json("POST", { trip_ids: tripIds }));
}

// Photos from `at` (ISO date or date-time) on move to a new trip. Resolves
// to [original, new].
export async function splitTrip(tripId, at) {
  const data = await request(`/trips/${tripId}/split`, json("POST", { at }));
  return data.trips;
}

export async function dismissTrip(tripId) {
  await request(`/trips/${tripId}/dismiss`, { method: "POST" });
}

export async function restoreTrip(tripId) {
  return request(`/trips/${tripId}/restore`, { method: "POST" });
}

// Home locations trip detection compares photos with. Every change resolves
// to {home, rebuild_started}: false means another job was running and trips
// must be rebuilt by hand for it to take effect.
export async function fetchHomes() {
  const data = await request("/homes");
  return data.homes;
}

export async function createHome(fields) {
  return request("/homes", json("POST", fields));
}

export async function updateHome(homeId, fields) {
  return request(`/homes/${homeId}`, json("PATCH", fields));
}

export async function confirmHome(homeId) {
  return request(`/homes/${homeId}/confirm`, { method: "POST" });
}

export async function deleteHome(homeId) {
  return request(`/homes/${homeId}`, { method: "DELETE" });
}

// Ids of every photo in the trip, oldest first.
export async function fetchTripPhotoIds(tripId) {
  const data = await request(`/trips/${tripId}/photos`);
  return data.photo_ids;
}

// What the trip map draws: {stops, route, home, unlocated_count}.
export async function fetchTripLocations(tripId) {
  return request(`/trips/${tripId}/locations`);
}

// Non-destructive backfill: adds cat/dog boxes to photos indexed before pet
// detection existed. Human faces and their tags are never touched.
export async function refreshPets() {
  return request("/pets/refresh", { method: "POST" });
}

export async function fetchTags() {
  const data = await request("/tags");
  return data.tags;
}

export async function createTag(name) {
  return request("/tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function updateTag(tagId, name) {
  return request(`/tags/${tagId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export async function deleteTag(tagId) {
  await request(`/tags/${tagId}`, { method: "DELETE" });
}

// The sidebar's "By Tag" filter: ids of every photo with this tag attached.
export async function fetchTagPhotoIds(tagId) {
  const data = await request(`/tags/${tagId}/photos`);
  return data.photo_ids;
}

export async function fetchPhotoTags(photoId) {
  const data = await request(`/photos/${photoId}/tags`);
  return data.tags;
}

export async function tagPhoto(photoId, tagId) {
  await request(`/photos/${photoId}/tags/${tagId}`, { method: "POST" });
}

export async function untagPhoto(photoId, tagId) {
  await request(`/photos/${photoId}/tags/${tagId}`, { method: "DELETE" });
}

// Downloads a CSV endpoint as a file, rather than navigating/opening a new
// tab -- fetched as a blob and handed to the browser via a throwaway
// <a download> element (clicked programmatically, then removed) so the
// download happens without ever leaving the page. Doesn't go through
// request() above since that assumes a JSON (or empty 204) body.
async function downloadCsv(path, filename) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`);
  } catch {
    throw new Error("Could not connect to the API. Is it running?");
  }

  if (!response.ok) {
    const isJson = (response.headers.get("content-type") || "").includes("application/json");
    const data = isJson ? await response.json().catch(() => null) : null;
    throw new Error(data?.detail || `Could not connect to the API (HTTP ${response.status}).`);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  } finally {
    URL.revokeObjectURL(url);
  }
}

// mode: "all" (default), "exact" (byte-identical only) or "similar"
// (resized/re-encoded/reformatted only) -- see GET /api/photos/duplicates/csv.
export async function downloadDuplicatesCsv(mode = "all") {
  await downloadCsv(`/photos/duplicates/csv?mode=${encodeURIComponent(mode)}`, "duplicate_photos.csv");
}

// The per-tag "List" button on the management page: every path+filename
// with this tag attached. The download is named after the tag (reduced to
// filename-safe characters) so several tags' lists don't all land as the
// same file.
export async function downloadTagCsv(tag) {
  const slug = tag.name.trim().replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "tag";
  await downloadCsv(`/tags/${tag.id}/photos/csv`, `${slug}.csv`);
}

export function thumbnailUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/thumbnail`;
}

// Where the API's connection to the photos backend stands: {status
// ("idle" | "connecting" | "ready" | "error"), message, steps: [{name,
// label, status, seconds}], error, elapsed_seconds}. Polled by the photo
// modal while an original is still loading.
export async function fetchBackendStatus() {
  return request("/backend/status");
}

export function originalUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/original`;
}

// Every background job (sync, face matching, descriptions, locations, pets, trips) in one
// response -- `active` names the one that holds the server's single job
// slot, `cancelling` is true between a cancel request and the job actually
// stopping. All browsers see the same thing, not just the one that started
// the job. Starting a job while another is active rejects with HTTP 409.
export async function fetchJobs() {
  return request("/jobs");
}

export async function cancelJob() {
  return request("/jobs/cancel", { method: "POST" });
}
