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

// Free-text similarity search over photos.description (see enrich.py/
// embeddings.py) -- results already come back ordered most-similar
// first (GET /api/photos/search), so no client-side sorting is needed.
export async function searchPhotos(query, threshold) {
  const params = new URLSearchParams({ q: query, threshold: String(threshold) });
  const data = await request(`/photos/search?${params}`);
  return data.results;
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

export async function fetchSyncStatus() {
  return request("/sync/status");
}

// The server may answer with a normal {reply, boxes} JSON body, or (when
// LUMINARY_OPENAI_STREAMING is enabled server-side) a streamed
// text/plain body delivering the reply incrementally -- which one it did
// is read from the response's own content-type, not decided here. Either
// way this resolves to {reply, boxes} (boxes always [] for a streamed
// reply -- streaming is openai-only, and only florence ever returns
// boxes); pass onChunk to also be notified of each reply piece as it
// arrives (never called for a JSON reply).
export async function chatAboutPhoto(photoId, backend, message, history, onChunk) {
  let response;
  try {
    response = await fetch(`${API_BASE}/photos/${photoId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ backend, message, history }),
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
    return { reply: data.reply, boxes: data.boxes || [] };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    full += chunk;
    onChunk?.(chunk);
  }
  return { reply: full, boxes: [] };
}

export async function fetchFaces(photoId) {
  const data = await request(`/photos/${photoId}/faces`);
  return data.faces;
}

export async function fetchIndividuals() {
  const data = await request("/individuals");
  return data.individuals;
}

export async function createIndividual(name) {
  return request("/individuals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
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

export async function downloadDuplicatesCsv() {
  await downloadCsv("/photos/duplicates/csv", "duplicate_photos.csv");
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

export function originalUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/original`;
}
