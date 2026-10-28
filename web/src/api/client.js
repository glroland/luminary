const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok && response.status !== 204) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response;
}

export async function fetchPhotos() {
  const response = await request("/photos");
  const data = await response.json();
  return data.photos;
}

export async function uploadPhoto(file) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await request("/photos", { method: "POST", body: formData });
  return response.json();
}

export async function deletePhoto(photoId) {
  await request(`/photos/${photoId}`, { method: "DELETE" });
}

export function thumbnailUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/thumbnail`;
}

export function originalUrl(photoId) {
  return `${API_BASE}/photos/${photoId}/original`;
}
