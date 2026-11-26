import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client.js";
import TripsManagement from "./TripsManagement.jsx";

const DETECTED = {
  id: "h1",
  name: "Atlanta",
  latitude: 33.749,
  longitude: -84.388,
  radius_km: null,
  start_date: null,
  end_date: null,
  city: "Atlanta",
  state: "Georgia",
  country: "United States",
  source: "auto",
  confirmed: false,
};

const DISMISSED = {
  id: "d1",
  name: "Lake weekend",
  kind: "trip",
  start_at: "2019-07-03T09:00:00",
  end_at: "2019-07-05T17:00:00",
  photo_count: 12,
};

describe("TripsManagement", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(client, "fetchHomes").mockResolvedValue([DETECTED]);
    vi.spyOn(client, "fetchDismissedTrips").mockResolvedValue([]);
  });

  it("asks whether a detected home is right, and confirms it", async () => {
    const confirm = vi.spyOn(client, "confirmHome").mockResolvedValue({ home: { ...DETECTED, confirmed: true }, rebuild_started: true });
    render(<TripsManagement jobs={null} />);

    expect(await screen.findByText("Is this home?")).toBeInTheDocument();
    client.fetchHomes.mockResolvedValue([{ ...DETECTED, confirmed: true }]);
    fireEvent.click(screen.getByRole("button", { name: /yes, that.s home/i }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("h1"));
    await waitFor(() => expect(screen.queryByText("Is this home?")).not.toBeInTheDocument());
    expect(screen.getByText("Home confirmed.")).toBeInTheDocument();
    expect(screen.getByText("confirmed")).toBeInTheDocument();
  });

  it("explains when there is no home yet", async () => {
    client.fetchHomes.mockResolvedValue([]);
    render(<TripsManagement jobs={null} />);
    expect(await screen.findByText(/no home found yet/i)).toBeInTheDocument();
  });

  it("edits a home and sends every field, clearing the ones left blank", async () => {
    const update = vi.spyOn(client, "updateHome").mockResolvedValue({ home: DETECTED, rebuild_started: true });
    render(<TripsManagement jobs={null} />);
    await screen.findByText("Atlanta");

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const row = within(screen.getByRole("button", { name: "Save" }).closest("li"));
    fireEvent.change(row.getByLabelText("Home name"), { target: { value: "Flat" } });
    fireEvent.change(row.getByLabelText("Radius in km"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("h1", {
        name: "Flat",
        latitude: 33.749,
        longitude: -84.388,
        radius_km: 25,
        start_date: null,
        end_date: null,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument());
  });

  it("adds a home by coordinates", async () => {
    client.fetchHomes.mockResolvedValue([]);
    const create = vi.spyOn(client, "createHome").mockResolvedValue({ home: { ...DETECTED }, rebuild_started: true });
    render(<TripsManagement jobs={null} />);
    await screen.findByText(/no home found yet/i);

    fireEvent.click(screen.getByText("Add a home"));
    const submit = screen.getByRole("button", { name: "Add home" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Latitude"), { target: { value: "36.2" } });
    fireEvent.change(screen.getByLabelText("Longitude"), { target: { value: "-81.6" } });
    fireEvent.change(screen.getByLabelText("Lived here from"), { target: { value: "2020-01-01" } });
    fireEvent.click(submit);

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: null,
        latitude: 36.2,
        longitude: -81.6,
        radius_km: null,
        start_date: "2020-01-01",
        end_date: null,
      }),
    );
  });

  it("removes a home", async () => {
    const remove = vi.spyOn(client, "deleteHome").mockResolvedValue({ home: null, rebuild_started: true });
    render(<TripsManagement jobs={null} />);
    await screen.findByText("Atlanta");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("h1"));
  });

  it("says so when a change couldn't rebuild trips because another job was running", async () => {
    vi.spyOn(client, "deleteHome").mockResolvedValue({ home: null, rebuild_started: false });
    render(<TripsManagement jobs={null} />);
    await screen.findByText("Atlanta");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByText(/another job was running/i)).toBeInTheDocument();
  });

  it("shows a server error and leaves the form open", async () => {
    vi.spyOn(client, "updateHome").mockRejectedValue(new Error("latitude must be between -90 and 90"));
    render(<TripsManagement jobs={null} />);
    await screen.findByText("Atlanta");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("latitude must be between");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("makes a trip from a date range", async () => {
    const create = vi.spyOn(client, "createTrip").mockResolvedValue({ id: "t", name: "Lake", photo_count: 7 });
    const onTripsChanged = vi.fn();
    render(<TripsManagement jobs={null} onTripsChanged={onTripsChanged} />);
    await screen.findByText("Atlanta");

    const submit = screen.getByRole("button", { name: "Make trip" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Trip name"), { target: { value: "Lake" } });
    fireEvent.change(screen.getByLabelText("Trip from"), { target: { value: "2018-07-03" } });
    fireEvent.click(submit);

    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: "Lake", startDate: "2018-07-03", endDate: "2018-07-03" }));
    expect(await screen.findByText(/made “Lake” with 7 photos/i)).toBeInTheDocument();
    expect(onTripsChanged).toHaveBeenCalled();
  });

  it("lists dismissed trips and restores one", async () => {
    client.fetchDismissedTrips.mockResolvedValue([DISMISSED]);
    const restore = vi.spyOn(client, "restoreTrip").mockResolvedValue(DISMISSED);
    const onTripsChanged = vi.fn();
    render(<TripsManagement jobs={null} onTripsChanged={onTripsChanged} />);

    expect(await screen.findByText("Lake weekend")).toBeInTheDocument();
    expect(screen.getByText(/12 photos/)).toBeInTheDocument();
    client.fetchDismissedTrips.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() => expect(restore).toHaveBeenCalledWith("d1"));
    await waitFor(() => expect(screen.getByText(/no dismissed trips/i)).toBeInTheDocument());
    expect(onTripsChanged).toHaveBeenCalled();
  });

  it("reloads the homes when a trips rebuild ends", async () => {
    const { rerender } = render(<TripsManagement jobs={{ active: "trips" }} />);
    await screen.findByText("Atlanta");
    client.fetchHomes.mockClear();

    rerender(<TripsManagement jobs={{ active: null }} />);

    await waitFor(() => expect(client.fetchHomes).toHaveBeenCalledTimes(1));
  });
});
