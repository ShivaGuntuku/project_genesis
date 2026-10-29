import * as echarts from "echarts";
import { useEffect, useRef, useState } from "react";

/** Read a CSS token (so charts follow the light/dark theme). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
export const series = (i: number) => token(`--series-${i}`);

function useScheme(): string {
  const [scheme, setScheme] = useState(() => matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: light)");
    const on = () => setScheme(mq.matches ? "light" : "dark");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return scheme;
}

export interface Line { name: string; color: string; data: [number, number][]; area?: boolean }

/** Common chrome: recessive hairline grid and axes, system font, no chart junk. */
function base(): echarts.EChartsOption {
  const muted = token("--muted"), grid = token("--grid"), axis = token("--axis");
  return {
    animation: false,
    textStyle: { fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", color: muted },
    grid: { left: 44, right: 52, top: 30, bottom: 24 },
    xAxis: {
      type: "value", minInterval: 1, axisLine: { lineStyle: { color: axis } }, axisTick: { show: false },
      splitLine: { show: false }, axisLabel: { color: muted, formatter: (v: number) => `${v}` },
    },
    yAxis: {
      type: "value", axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: grid, width: 1 } }, axisLabel: { color: muted },
    },
    tooltip: {
      backgroundColor: token("--surface-2"), borderColor: token("--border"),
      textStyle: { color: token("--ink"), fontSize: 12 },
    },
  };
}

const fmt = (v: number, percent?: boolean) =>
  percent ? `${Math.round(v * 100)}%` : Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}K` : `${+v.toFixed(2)}`;

/** Lines over days: crosshair tooltip listing every series; legend for 2+; end labels. */
export function LineChart({ lines, xName = "day", percent = false, loading = false, height = 220, yMin, yMax }: {
  lines: Line[]; xName?: string; percent?: boolean; loading?: boolean; height?: number; yMin?: number; yMax?: number;
}) {
  const scheme = useScheme();
  const option = (): echarts.EChartsOption => {
    const b = base();
    const surface = token("--surface-1");
    return {
      ...b,
      legend: lines.length > 1 ? { top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 3,
        itemGap: 14, textStyle: { color: token("--ink-2") } } : undefined,
      grid: { ...(b.grid as object), top: lines.length > 1 ? 30 : 12 },
      xAxis: { ...(b.xAxis as object), min: (v: { min: number }) => Math.floor(v.min),
        max: (v: { max: number }) => Math.ceil(v.max) } as echarts.XAXisComponentOption,
      yAxis: { ...(b.yAxis as object), min: yMin, max: yMax,
        axisLabel: { color: token("--muted"), formatter: (v: number) => fmt(v, percent) } } as echarts.YAXisComponentOption,
      tooltip: {
        ...(b.tooltip as object), trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: token("--axis") } },
        formatter: (params: unknown) => {
          const ps = params as { seriesName: string; color: string; value: [number, number]; axisValue: number }[];
          if (!ps.length) return "";
          const rows = ps.map((p) =>
            `<div style="display:flex;gap:8px;align-items:center"><span style="display:inline-block;width:14px;height:2px;background:${p.color}"></span>` +
            `<b>${fmt(p.value[1], percent)}</b><span style="opacity:.75">${p.seriesName}</span></div>`).join("");
          return `<div style="opacity:.75;margin-bottom:2px">${xName} ${ps[0].value[0]}</div>${rows}`;
        },
      },
      series: lines.map((l) => ({
        name: l.name, type: "line", data: l.data, showSymbol: false, symbolSize: 8,
        lineStyle: { width: 2, color: l.color, cap: "round", join: "round" }, itemStyle: { color: l.color, borderColor: surface, borderWidth: 2 },
        areaStyle: l.area ? { color: l.color, opacity: 0.1 } : undefined,
        emphasis: { focus: "series" },
        endLabel: lines.length <= 4 ? { show: true, color: token("--ink-2"), fontSize: 11,
          formatter: (p: { value?: unknown }) => fmt((p.value as [number, number])[1], percent) } : undefined,
      })),
    };
  };
  return <EChart option={option} deps={[lines, scheme, percent]} loading={loading} height={height} />;
}

/** Columns per day (optionally stacked), with a 2px surface gap and 4px rounded tops. */
export function ColumnChart({ bars, stacked = false, loading = false, height = 220, xName = "day" }: {
  bars: { name: string; color: string; data: [number, number][] }[]; stacked?: boolean; loading?: boolean; height?: number;
  xName?: string;
}) {
  const scheme = useScheme();
  const option = (): echarts.EChartsOption => {
    const b = base();
    const surface = token("--surface-1");
    const days = [...new Set(bars.flatMap((s) => s.data.map(([d]) => d)))].sort((x, y) => x - y);
    return {
      ...b,
      legend: bars.length > 1 ? { top: 0, left: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: token("--ink-2") } } : undefined,
      grid: { ...(b.grid as object), top: bars.length > 1 ? 30 : 12, right: 16 },
      xAxis: { ...(b.xAxis as object), type: "category", data: days.map(String) } as echarts.XAXisComponentOption,
      tooltip: {
        ...(b.tooltip as object), trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: token("--grid"), opacity: 0.5 } },
        formatter: (params: unknown) => {
          const all = params as { seriesName: string; color: string; value: number; axisValue: string }[];
          const ps = all.filter((p) => p.value);
          if (!all.length) return "";
          return `<div style="opacity:.75;margin-bottom:2px">${xName} ${all[0].axisValue}</div>` + (ps.length ? ps.slice().reverse().map((p) =>
            `<div style="display:flex;gap:8px;align-items:center"><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color}"></span>` +
            `<b>${p.value}</b><span style="opacity:.75">${p.seriesName}</span></div>`).join("") : "<b>0</b>");
        },
      },
      series: bars.map((s, i) => {
        const byDay = new Map(s.data);
        return {
          name: s.name, type: "bar", stack: stacked ? "all" : undefined, data: days.map((d) => byDay.get(d) ?? 0),
          barMaxWidth: 24,
          itemStyle: { color: s.color, borderColor: surface, borderWidth: stacked ? 1 : 0,
            borderRadius: !stacked || i === bars.length - 1 ? [4, 4, 0, 0] : 0 },
        };
      }),
    };
  };
  return <EChart option={option} deps={[bars, scheme, stacked, xName]} loading={loading} height={height} />;
}

function EChart({ option, deps, loading, height }: {
  option: () => echarts.EChartsOption; deps: unknown[]; loading: boolean; height: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "svg" });
    const resize = new ResizeObserver(() => chart.current?.resize());
    resize.observe(ref.current);
    return () => { resize.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option(), true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return <div ref={ref} className={`chart${loading ? " loading" : ""}`} style={{ height }} />;
}
