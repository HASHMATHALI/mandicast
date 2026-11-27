import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ForecastResponse, PricesResponse } from "../api/types";
import { dayNumber } from "../lib/chart";
import forecastH7 from "../test/fixtures/forecast_onion_199_h7.json";
import prices from "../test/fixtures/prices_onion_199_1m.json";
import { PriceChart } from "./PriceChart";

const history = (prices as unknown as PricesResponse).items;
const fc = forecastH7 as unknown as ForecastResponse;
const start = dayNumber("2026-10-22");

describe("PriceChart (recorded Lasalgaon onion data)", () => {
  it("keeps history, forecast and ranges as separate marks", () => {
    const { container } = render(
      <PriceChart history={history} forecast={fc} level={0.8} showMinMax={false} startDay={start} label="chart" />);
    // 21 reports with one gap of 12 days (4–16 Nov 2026): two separate line runs, never joined across the gap.
    expect(screen.getAllByTestId("history-run")).toHaveLength(2);
    expect(container.querySelector(".history-line")).not.toBeNull();
    // The forecast is its own dashed line, and only the 80% range is drawn at the 80% level.
    expect(screen.getByTestId("forecast-line")).toHaveClass("forecast-line");
    expect(screen.getByTestId("band-80")).toBeInTheDocument();
    expect(screen.queryByTestId("band-90")).toBeNull();
    // The latest report is labelled with its value and date; the issue date is marked.
    expect(container.textContent).toContain("₹3,650 · 26 Nov");
    expect(container.textContent).toContain("Forecast issued 23 Nov");
  });

  it("adds the 90% range only when asked", () => {
    render(<PriceChart history={history} forecast={fc} level={0.9} showMinMax={false} startDay={start} label="c" />);
    expect(screen.getByTestId("band-90")).toBeInTheDocument();
    expect(screen.getByTestId("band-80")).toBeInTheDocument();
  });

  it("draws history alone when there is no forecast", () => {
    render(<PriceChart history={history} forecast={null} level={0.8} showMinMax label="c" startDay={start} />);
    expect(screen.queryByTestId("forecast-line")).toBeNull();
    expect(screen.queryByTestId("band-80")).toBeNull();
  });

  it("reads values from the keyboard (same readout as the hover tooltip)", () => {
    render(<PriceChart history={history} forecast={fc} level={0.8} showMinMax={false} startDay={start}
                       label="Onion modal price" />);
    const svg = screen.getByRole("img", { name: "Onion modal price" });
    fireEvent.keyDown(svg, { key: "End" });
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("30 Nov 2026");
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("₹4,043");
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("₹3,738–₹4,575");
    fireEvent.keyDown(svg, { key: "Home" });
    expect(screen.getByTestId("chart-tooltip")).toHaveTextContent("22 Oct 2026");
    fireEvent.keyDown(svg, { key: "Escape" });
    expect(screen.queryByTestId("chart-tooltip")).toBeNull();
  });
});
