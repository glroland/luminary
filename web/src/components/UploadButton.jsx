import { useRef, useState } from "react";
import { uploadPhoto } from "../api/client.js";

export default function UploadButton({ onUploaded }) {
  const inputRef = useRef(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState(null);

  async function handleChange(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;

    setIsUploading(true);
    setError(null);
    try {
      const photo = await uploadPhoto(file);
      onUploaded(photo);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="upload-button">
      <button type="button" disabled={isUploading} onClick={() => inputRef.current?.click()}>
        {isUploading ? "Uploading..." : "Upload Photo"}
      </button>
      <input ref={inputRef} type="file" accept="image/*" hidden onChange={handleChange} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
