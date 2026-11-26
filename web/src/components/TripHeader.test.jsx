import { fireEvent, render, screen } from "@testing-library/react";
import L from "leaflet";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TripHeader from "./TripHeader.jsx";

const TRIP = {
  id: "t1",
  name: "Boone, North Carolina, May 2019",
  kind: "trip",
  start_at: "2019-05-10T09:00:00",
  end_at: "2019-05-12T17:00:00",
  photo_count: 15,
};

const LOCATIONS = {
  stops: [
    { latitude: 36.2, longitude: -81.6, count: 9, first_at: "2019-05-10T09:00:00", last_at: "2019-05-11T10:00:00", place: "Boone Cafe", cover_photo_id: "p1", photo_ids: ["p1", "p2"] },
    { latitude: 36.1, longitude: -81.7, count: 6, first_at: "2019-05-12T09:00:00", last_at: "2019-05-12T17:00:00", place: null, cover_photo_id: "p3", photo_ids: ["p3"] },
  ],
  route: [0, 1],
  home: { latitude: 33.7, longitude: -84.4, name: "Atlanta" },
  unlocated_count: 2,
};

describe("TripHeader", () => {
  beforeEach(() => {
    // jsdom has no SVG, which Leaflet's vector layers need to draw the
    // route; markers (plain DOM) are real.
    vi.spyOn(L, "polyline").mockReturnValue({ addTo: vi.fn() });
  });

  it("shows the trip's name, dates and size", () => {
    render(<TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} />);
    expect(screen.getByRole("heading", { name: TRIP.name })).toBeInTheDocument();
    expect(screen.getByText(/May 10 – 12, 2019 · 15 photos/)).toBeInTheDocument();
  });

  it("draws the map, and says how many photos have no location", () => {
    const { container } = render(<TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} />);
    expect(container.querySelector(".trip-map")).toBeInTheDocument();
    expect(container.querySelectorAll(".trip-stop-marker")).toHaveLength(2);
    expect(screen.getByText(/2 photos have no location/i)).toBeInTheDocument();
  });

  it("shows a loading note until the locations arrive", () => {
    render(<TripHeader trip={TRIP} locations={null} selectedStop={null} onSelectStop={vi.fn()} />);
    expect(screen.getByText(/loading map/i)).toBeInTheDocument();
  });

  it("says so when no photo in the trip has a location", () => {
    render(
      <TripHeader trip={TRIP} locations={{ stops: [], route: [], home: null, unlocated_count: 0 }} selectedStop={null} onSelectStop={vi.fn()} />,
    );
    expect(screen.getByText(/no location data for this trip/i)).toBeInTheDocument();
  });

  it("collapses and reopens the map", () => {
    const { container } = render(<TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Hide map" }));
    expect(container.querySelector(".trip-map")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show map" }));
    expect(container.querySelector(".trip-map")).toBeInTheDocument();
  });

  it("names the selected stop and offers the whole trip back", () => {
    const onSelectStop = vi.fn();
    render(<TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={0} onSelectStop={onSelectStop} />);
    expect(screen.getByText(/showing 9 photos at boone cafe/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /show the whole trip/i }));
    expect(onSelectStop).toHaveBeenCalledWith(null);
  });

  it("labels a day outing", () => {
    render(<TripHeader trip={{ ...TRIP, kind: "outing" }} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} />);
    expect(screen.getByText(/day outing/i)).toBeInTheDocument();
  });
});
