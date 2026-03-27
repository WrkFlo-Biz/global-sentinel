"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, IChartApi, ISeriesApi, CandlestickData, Time } from "lightweight-charts";

interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

interface Props {
  symbol: string;
  timeframe?: string;
  height?: number;
  entryPrice?: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function fetchBars(symbol: string, timeframe: string): Promise<Bar[]> {
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(
    `${API_BASE}/api/bars/${symbol}?timeframe=${timeframe}&limit=300`,
    { cache: "no-store", headers }
  );
  if (!res.ok) return [];
  const data = await res.json();
  return data.bars || [];
}

export default function CandlestickChart({ symbol, timeframe = "5Min", height = 350, entryPrice }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [priceChange, setPriceChange] = useState<number>(0);
  const [periodHigh, setPeriodHigh] = useState<number | null>(null);
  const [periodLow, setPeriodLow] = useState<number | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0e17" },
        textColor: "#9ca3af",
        fontFamily: "'SF Mono', 'Fira Code', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1a1f2e" },
        horzLines: { color: "#1a1f2e" },
      },
      crosshair: {
        vertLine: { color: "#3b82f6", width: 1, style: 2 },
        horzLine: { color: "#3b82f6", width: 1, style: 2 },
      },
      timeScale: {
        borderColor: "#2a3040",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#2a3040",
      },
      width: containerRef.current.clientWidth,
      height,
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderUpColor: "#10b981",
      borderDownColor: "#ef4444",
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });

    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;
    seriesRef.current = candleSeries;
    volumeRef.current = volumeSeries;

    // Load data
    loadBars(candleSeries, volumeSeries, chart);

    // Handle resize
    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [symbol, timeframe, height]);

  async function loadBars(
    candleSeries: ISeriesApi<"Candlestick">,
    volumeSeries: ISeriesApi<"Histogram">,
    chart: IChartApi
  ) {
    setLoading(true);
    setError("");
    try {
      const bars = await fetchBars(symbol, timeframe);
      if (bars.length === 0) {
        setError("No data");
        setLoading(false);
        return;
      }

      // Shift UTC timestamps to local time for display
      const toLocalTime = (t: string): Time => {
        const d = new Date(t);
        const utcSec = d.getTime() / 1000;
        const offsetSec = d.getTimezoneOffset() * -60; // negative because getTimezoneOffset returns inverse
        return (utcSec + offsetSec) as Time;
      };

      const candleData: CandlestickData[] = bars.map((b) => ({
        time: toLocalTime(b.t),
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      }));

      const volumeData = bars.map((b) => ({
        time: toLocalTime(b.t),
        value: b.v,
        color: b.c >= b.o ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)",
      }));

      candleSeries.setData(candleData);
      volumeSeries.setData(volumeData);

      // Entry price line
      if (entryPrice) {
        candleSeries.createPriceLine({
          price: entryPrice,
          color: "#f59e0b",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: "Entry",
        });
      }

      const last = bars[bars.length - 1];
      const first = bars[0];
      setLastPrice(last.c);
      setPriceChange(((last.c - first.o) / first.o) * 100);

      // Compute period high/low
      let hi = -Infinity, lo = Infinity;
      for (const b of bars) {
        if (b.h > hi) hi = b.h;
        if (b.l < lo) lo = b.l;
      }
      setPeriodHigh(hi);
      setPeriodLow(lo);

      // Draw high/low price lines
      candleSeries.createPriceLine({
        price: hi,
        color: "#10b981",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: "High",
      });
      candleSeries.createPriceLine({
        price: lo,
        color: "#ef4444",
        lineWidth: 1,
        lineStyle: 3,
        axisLabelVisible: true,
        title: "Low",
      });

      chart.timeScale().fitContent();
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  // Auto-refresh every 30s
  useEffect(() => {
    const interval = setInterval(() => {
      if (seriesRef.current && volumeRef.current && chartRef.current) {
        loadBars(seriesRef.current, volumeRef.current, chartRef.current);
      }
    }, 30000);
    return () => clearInterval(interval);
  }, [symbol, timeframe]);

  return (
    <div className="relative">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <span className="text-sm font-bold text-gray-200">{symbol}</span>
          {lastPrice !== null && (
            <>
              <span className="text-sm text-gray-300">${lastPrice.toFixed(2)}</span>
              <span className={`text-xs ${priceChange >= 0 ? "text-green-400" : "text-red-400"}`}>
                {priceChange >= 0 ? "+" : ""}{priceChange.toFixed(2)}%
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-4">
          {entryPrice ? (
            <span className="text-[11px] text-amber-400">Entry: ${entryPrice.toFixed(2)}</span>
          ) : null}
          {periodHigh !== null && (
            <span className="text-[11px] text-green-400">H: ${periodHigh.toFixed(2)}</span>
          )}
          {periodLow !== null && (
            <span className="text-[11px] text-red-400">L: ${periodLow.toFixed(2)}</span>
          )}
          {loading && (
            <span className="text-[10px] text-gray-500">Loading...</span>
          )}
        </div>
      </div>
      {error && (
        <div className="text-xs text-red-400 mb-2">{error}</div>
      )}
      <div ref={containerRef} style={{ width: "100%", height }} />
    </div>
  );
}
