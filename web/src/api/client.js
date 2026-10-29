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

export function thumbnailUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/thumbnail`;
}

export function originalUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/original`;
}
