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

export async function startSync() {
  return request("/sync", { method: "POST" });
}

export async function fetchSyncStatus() {
  return request("/sync/status");
}

export function thumbnailUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/thumbnail`;
}

export function originalUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/original`;
}
