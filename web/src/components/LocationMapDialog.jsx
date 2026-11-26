import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { bubbleSize, clusterPhotos, hasCoordinates } from "../locations.js";

// Full-viewport map for choosing photos by where they were taken. Photos
// with GPS coordinates are merged into bubbles (bigger = more photos);
// click bubbles to select them, then show the photos from those places.
export default function LocationMapDialog({ photos, initialSelection = null, onApply, onClose }) {
  const located = useMemo(() => photos.filter(hasCoordinates), [photos]);
  const [selected, setSelected] = useState(() => new Set(initialSelection ?? []));
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [zoomTick, setZoomTick] = useState(0);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const map = L.map(containerRef.current, { center: [30, 0], zoom: 2, worldCopyJump: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    if (located.length > 0) {
      map.fitBounds(L.latLngBounds(located.map((p) => [p.latitude, p.longitude])), { padding: [60, 60], maxZoom: 12 });
    }
    // Bubbles merge/split with the zoom level, so redraw whenever it changes.
    map.on("zoomend", () => setZoomTick((n) => n + 1));
    setZoomTick((n) => n + 1);
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;
    layer.clearLayers();
    const clusters = clusterPhotos(located, (lat, lng) => map.project([lat, lng]));
    const maxCount = Math.max(1, ...clusters.map((c) => c.count));
    for (const cluster of clusters) {
      const size = bubbleSize(cluster.count, maxCount);
      const isSelected = cluster.ids.every((id) => selected.has(id));
      const icon = L.divIcon({
        className: "",
        iconSize: [size, size],
        html: `<div class="map-bubble${isSelected ? " map-bubble-selected" : ""}" style="width:${size}px;height:${size}px" data-testid="map-bubble">${cluster.count}</div>`,
      });
      const marker = L.marker([cluster.latitude, cluster.longitude], {
        icon,
        title: `${cluster.count} photo${cluster.count === 1 ? "" : "s"}`,
        keyboard: false,
      });
      marker.on("click", () => {
        setSelected((prev) => {
          const next = new Set(prev);
          const all = cluster.ids.every((id) => next.has(id));
          for (const id of cluster.ids) {
            if (all) next.delete(id);
            else next.add(id);
          }
          return next;
        });
      });
      marker.addTo(layer);
    }
  }, [located, selected, zoomTick]);

  // Portaled to <body>: the sidebar's transform would otherwise contain a fixed overlay.
  return createPortal(
    <div className="map-dialog-backdrop" role="dialog" aria-modal="true" aria-label="Choose locations on map">
      <div className="map-dialog">
        <div className="map-dialog-header">
          <strong>Choose locations on the map</strong>
          <span className="map-dialog-hint">
            {located.length.toLocaleString()} photos with a location · click bubbles to select · drag to pan
          </span>
          <span className="map-dialog-spacer" />
          <span>{selected.size.toLocaleString()} selected</span>
          <button type="button" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
            Clear
          </button>
          <button type="button" className="map-dialog-primary" onClick={() => onApply(selected)} disabled={selected.size === 0}>
            Show photos
          </button>
          <button type="button" onClick={onClose} aria-label="Close map">
            ✕
          </button>
        </div>
        <div className="map-dialog-map" ref={containerRef} />
      </div>
    </div>,
    document.body,
  );
}
