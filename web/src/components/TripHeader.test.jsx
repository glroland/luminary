import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  describe("editing", () => {
    const others = [
      { id: "t2", name: "Florida, Dec 2019" },
      { id: "t3", name: "Italy, May 2020" },
    ];
    const setup = (props = {}) => {
      const handlers = {
        onRename: vi.fn().mockResolvedValue(),
        onMerge: vi.fn().mockResolvedValue(),
        onSplit: vi.fn().mockResolvedValue(),
        onDismiss: vi.fn().mockResolvedValue(),
      };
      render(
        <TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} otherTrips={others} {...handlers} {...props} />,
      );
      return handlers;
    };

    it("has no edit buttons unless it can edit", () => {
      render(<TripHeader trip={TRIP} locations={LOCATIONS} selectedStop={null} onSelectStop={vi.fn()} />);
      expect(screen.queryByRole("button", { name: "Rename" })).not.toBeInTheDocument();
    });

    it("renames", async () => {
      const { onRename } = setup();
      fireEvent.click(screen.getByRole("button", { name: "Rename" }));
      fireEvent.change(screen.getByLabelText("Trip name"), { target: { value: "Mountain weekend" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(onRename).toHaveBeenCalledWith("Mountain weekend"));
      await waitFor(() => expect(screen.queryByLabelText("Trip name")).not.toBeInTheDocument());
    });

    it("offers the generated name back only after a rename", async () => {
      const { onRename } = setup({ trip: { ...TRIP, name_source: "manual" } });
      fireEvent.click(screen.getByRole("button", { name: "Rename" }));
      fireEvent.click(screen.getByRole("button", { name: /use the generated name/i }));
      await waitFor(() => expect(onRename).toHaveBeenCalledWith(""));
    });

    it("merges with the chosen trip", async () => {
      const { onMerge } = setup();
      fireEvent.click(screen.getByRole("button", { name: /merge with/i }));
      fireEvent.change(screen.getByLabelText("Trip to merge with"), { target: { value: "t3" } });
      fireEvent.click(screen.getByRole("button", { name: "Merge" }));
      await waitFor(() => expect(onMerge).toHaveBeenCalledWith("t3"));
    });

    it("can't merge when there is no other trip", () => {
      setup({ otherTrips: [] });
      expect(screen.getByRole("button", { name: /merge with/i })).toBeDisabled();
    });

    it("splits at a date inside the trip", async () => {
      const { onSplit } = setup();
      fireEvent.click(screen.getByRole("button", { name: /split/i }));
      const input = screen.getByLabelText("Split from date");
      expect(input).toHaveAttribute("min", "2019-05-10");
      expect(input).toHaveAttribute("max", "2019-05-12");
      expect(screen.getByRole("button", { name: "Split" })).toBeDisabled();
      fireEvent.change(input, { target: { value: "2019-05-11" } });
      fireEvent.click(screen.getByRole("button", { name: "Split" }));
      await waitFor(() => expect(onSplit).toHaveBeenCalledWith("2019-05-11"));
    });

    it("dismisses only after confirming", async () => {
      const { onDismiss } = setup();
      const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      expect(onDismiss).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
      await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1));
      expect(confirm).toHaveBeenCalledTimes(2);
    });

    it("shows why an edit failed and keeps the panel open", async () => {
      setup({ onSplit: vi.fn().mockRejectedValue(new Error("Both halves of a split need at least one photo")) });
      fireEvent.click(screen.getByRole("button", { name: /split/i }));
      fireEvent.change(screen.getByLabelText("Split from date"), { target: { value: "2019-05-11" } });
      fireEvent.click(screen.getByRole("button", { name: "Split" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("Both halves");
      expect(screen.getByLabelText("Split from date")).toBeInTheDocument();
    });
  });
});
