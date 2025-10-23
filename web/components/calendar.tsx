"use client";

import * as d3 from "d3";
import { useEffect, useRef } from "react";

type Datum = { date: Date; count: number };
type DatumToStringFunction = (d: Datum) => string;

export default function Calendar({ data }: { data: Datum[] }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      // Clear any existing content
      containerRef.current.innerHTML = "";

      // Create the SVG element using D3
      const svg = CalendarBuilder(data);

      // Append the SVG to the container
      if (svg) {
        containerRef.current.appendChild(svg);
      }
    }

    // Optional: Cleanup function
    return () => {
      if (containerRef.current) {
        containerRef.current.innerHTML = "";
      }
    };
  }, []); // Empty dependency array ensures this runs once on mount

  return <div ref={containerRef}></div>;
}

function CalendarBuilder(
  data: Datum[],
  {
    x = (d: Datum) => d.date, // given d in data, returns the (temporal) x-value
    y = (d: Datum) => d.count, // given d in data, returns the (quantitative) y-value
    width = 1000, // width of the chart, in pixels
    cellSize = 17, // width and height of an individual day, in pixels
    weekday = "sunday", // either: weekday, sunday, or monday
    formatDay = (i: number) => "SMTWTFS"[i], // given a day number in [0, 6], the day-of-week label
    fmtMonth = "%b", // format specifier string for months (above the chart)
    yFormat: string = "%b", // format specifier string for values (in the title)
    colors = d3.interpolatePiYG,
  } = {}
) {
  data = processDates(data);
  // Compute values.
  const X = d3.map(data, x);
  const Y = d3.map(data, y);
  const I = d3.range(X.length);

  const countDay =
    weekday === "sunday" ? (i: number) => i : (i: number) => (i + 6) % 7;
  const timeWeek = weekday === "sunday" ? d3.utcSunday : d3.utcMonday;
  const weekDays = weekday === "weekday" ? 5 : 7;
  const height = cellSize * (weekDays + 2);

  // Compute a color scale. This assumes a diverging color scheme where the pivot
  // is zero, and we want symmetric difference around zero.
  const max = d3.quantile(Y, 0.9975, Math.abs) || 1;
  const color = d3.scaleSequential([-max, +max], colors).unknown("none");

  // Construct formats.
  const formatMonth = d3.utcFormat(fmtMonth);

  // Compute titles.
  color.apply;
  const formatDate = d3.utcFormat("%B %-d, %Y");
  const title = (i: number) => `${formatDate(X[i])}\n${Y[i]}`;

  // Group the index by year, in reverse input order. (Assuming that the input is
  // chronological, this will show years in reverse chronological order.)
  const years = d3.groups(I, (i) => X[i].getUTCFullYear()).reverse();

  function pathMonth(t: Date) {
    const d = Math.max(0, Math.min(weekDays, countDay(t.getUTCDay())));
    const w = timeWeek.count(d3.utcYear(t), t);
    return `${
      d === 0
        ? `M${w * cellSize},0`
        : d === weekDays
        ? `M${(w + 1) * cellSize},0`
        : `M${(w + 1) * cellSize},0V${d * cellSize}H${w * cellSize}`
    }V${weekDays * cellSize}`;
  }

  const svg = d3
    .create("svg")
    .attr("width", width)
    .attr("height", height * years.length)
    .attr("viewBox", [0, 0, width, height * years.length])
    .attr("style", "max-width: 100%; height: auto; height: intrinsic;")
    .attr("font-family", "sans-serif")
    .attr("font-size", 10);

  const year = svg
    .selectAll("g")
    .data(years)
    .join("g")
    .attr(
      "transform",
      (d, i) => `translate(40.5,${height * i + cellSize * 1.5})`
    );

  year
    .append("text")
    .attr("x", -5)
    .attr("y", -5)
    .attr("font-weight", "bold")
    .attr("text-anchor", "end")
    .text(([key]) => key);

  year
    .append("g")
    .attr("text-anchor", "end")
    .selectAll("text")
    .data(weekday === "weekday" ? d3.range(1, 6) : d3.range(7))
    .join("text")
    .attr("x", -5)
    .attr("y", (i) => (countDay(i) + 0.5) * cellSize)
    .attr("dy", "0.31em")
    .text(formatDay);

  const cell = year
    .append("g")
    .selectAll("rect")
    .data(
      weekday === "weekday"
        ? ([, I]) => I.filter((i) => ![0, 6].includes(X[i].getUTCDay()))
        : ([, I]) => I
    )
    .join("rect")
    .attr("width", cellSize - 1)
    .attr("height", cellSize - 1)
    .attr("x", (i) => timeWeek.count(d3.utcYear(X[i]), X[i]) * cellSize + 0.5)
    .attr("y", (i) => countDay(X[i].getUTCDay()) * cellSize + 0.5)
    .attr("fill", (i) => color(Y[i]));

  if (title) cell.append("title").text(title);

  const month = year
    .append("g")
    .selectAll("g")
    .data(([, I]) => d3.utcMonths(d3.utcMonth(X[I[0]]), X[I[I.length - 1]]))
    .join("g");

  month
    .filter((d: Date, i: number) => i > 0)
    .append("path")
    .attr("fill", "none")
    .attr("stroke", "#fff")
    .attr("stroke-width", 3)
    .attr("d", pathMonth);

  month
    .append("text")
    .attr(
      "x",
      (d) => timeWeek.count(d3.utcYear(d), timeWeek.ceil(d)) * cellSize + 2
    )
    .attr("y", -5)
    .text(formatMonth);

  // return Object.assign(svg.node() || {}, { scales: { color } });

  return svg.node();
}

function processDates(data: Datum[]): Datum[] {
  // Reverse the array
  const reversed = [...data].reverse();

  // Consolidate groups on the same day
  const consolidated: Record<string, number> = {};
  reversed.forEach((item) => {
    const dateStr = item.date.toISOString().split("T")[0];
    consolidated[dateStr] = (consolidated[dateStr] || 0) + item.count;
  });

  const consolidatedData: Datum[] = Object.entries(consolidated).map(
    ([date, count]) => ({ date: new Date(date), count })
  );

  // Fill in the days between the first and last date with 0 counts
  const sortedDates = Object.keys(consolidated).sort();
  const filledData: Datum[] = [];
  let currentDate = new Date(sortedDates[0]);

  while (currentDate <= new Date(sortedDates[sortedDates.length - 1])) {
    const dateStr = currentDate.toISOString().split("T")[0];
    filledData.push({
      date: new Date(dateStr),
      count: consolidated[dateStr] || 0,
    });

    // Move to next day
    currentDate = new Date(currentDate.setDate(currentDate.getDate() + 1));
  }

  return filledData;
}
