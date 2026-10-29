import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Leaflet's default marker icon references image paths that don't resolve
// through Vite's bundler -- point them at the bundled assets explicitly.
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

export default function PhotoMap({ latitude, longitude }) {
  const containerRef = useRef(null);

  const hasLocation = latitude != null && longitude != null;

  useEffect(() => {
    if (!hasLocation || !containerRef.current) {
      return undefined;
    }

    const map = L.map(containerRef.current, {
      center: [latitude, longitude],
      zoom: 13,
      scrollWheelZoom: false,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);

    L.marker([latitude, longitude]).addTo(map);

    return () => map.remove();
  }, [hasLocation, latitude, longitude]);

  if (!hasLocation) {
    return (
      <div className="photo-map-empty" role="status">
        No location data for this photo.
      </div>
    );
  }

  return <div className="photo-map" ref={containerRef} />;
}
