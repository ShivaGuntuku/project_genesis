import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import { go, useApi } from "../api";
import { token } from "../chart";
import { Card, Status } from "../components";

interface Node { id: number; name: string; generation: number; stage: string; group: number | null;
  groupName: string | null; centrality: number; community: number | null }
interface Edge { source: number; target: number; kind: "friend" | "rival" | "family" | "partner"; label: string }

const EDGE_STYLE: Record<Edge["kind"], { color: string; width: number; type: "solid" | "dashed" }> = {
  friend: { color: "--muted", width: 1, type: "solid" },
  family: { color: "--ink-2", width: 2, type: "solid" },
  partner: { color: "--ink", width: 2.5, type: "solid" },
  rival: { color: "--lie", width: 1.5, type: "dashed" },
};

export default function Network() {
  const { data, error, loading } = useApi<{ nodes: Node[]; edges: Edge[]; clock: string }>("/api/network");
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "svg" });
    chart.current.on("click", (p) => {
      const d = p.data as { id?: number } | undefined;
      if (p.dataType === "node" && d?.id) go(`#/agent/${d.id}`);
    });
    const resize = new ResizeObserver(() => chart.current?.resize());
    resize.observe(ref.current);
    return () => { resize.disconnect(); chart.current?.dispose(); };
  }, []);

  useEffect(() => {
    if (!data || !chart.current) return;
    // Colour follows the group (fixed order by group id, never cycled); no group = grey.
    const groupIds = [...new Set(data.nodes.map((n) => n.group).filter((g): g is number => g !== null))].sort((a, b) => a - b);
    const categories = [
      ...groupIds.slice(0, 7).map((id, i) => ({
        name: data.nodes.find((n) => n.group === id)?.groupName ?? `group ${id}`,
        itemStyle: { color: token(`--series-${i + 1}`) },
      })),
      ...(groupIds.length > 7 ? [{ name: "other groups", itemStyle: { color: token("--series-8") } }] : []),
      { name: "no group", itemStyle: { color: token("--muted") } },
    ];
    const catOf = (n: Node) => {
      if (n.group === null) return categories.length - 1;
      const i = groupIds.indexOf(n.group);
      return i < 7 ? i : 7;
    };
    chart.current.setOption({
      animation: false,
      tooltip: {
        backgroundColor: token("--surface-2"), borderColor: token("--border"), textStyle: { color: token("--ink"), fontSize: 12 },
        formatter: (p: { dataType: string; data: Record<string, unknown> }) => {
          const d = p.data;
          if (p.dataType === "edge") return `${d.kind}`;
          return `<b>${d.name}</b><br/>${d.groupName ?? "no group"} · gen ${d.generation} · ${d.stage}` +
            `<br/>friends reach: ${d.centrality}${d.community ? ` · community ${d.community}` : ""}`;
        },
      },
      legend: [{ data: categories.map((c) => c.name), top: 0, left: 0, textStyle: { color: token("--ink-2") } }],
      series: [{
        type: "graph", layout: "force", roam: true, draggable: true,
        force: { repulsion: 220, edgeLength: [40, 120], gravity: 0.08 },
        categories,
        label: { show: true, position: "right", color: token("--ink-2"), fontSize: 11 },
        lineStyle: { opacity: 0.7, curveness: 0.1 },
        emphasis: { focus: "adjacency" },
        data: data.nodes.map((n) => ({
          ...n, id: String(n.id), name: n.name, category: catOf(n),
          symbolSize: 10 + 26 * n.centrality, itemStyle: { borderColor: token("--surface-1"), borderWidth: 2 },
        })),
        links: data.edges.map((e) => {
          const s = EDGE_STYLE[e.kind];
          return { source: String(e.source), target: String(e.target), kind: e.kind,
            lineStyle: { color: token(s.color), width: s.width, type: s.type } };
        }),
      }],
    }, true);
  }, [data]);

  return (
    <>
      <h1>Network</h1>
      <p className="sub">
        The living society{data ? ` at ${data.clock}` : ""}. Colour = group; size = how many friends (centrality).
        Lines: <b>grey</b> friends · <b>bold</b> family · <b>boldest</b> partners · <b>red dashed</b> rivals and enemies.
        Drag to arrange, scroll to zoom, click someone to open them.
      </p>
      <Status error={error} loading={loading} empty={data?.nodes.length === 0} />
      <Card title="Who is connected to whom">
        <div ref={ref} style={{ width: "100%", height: 620, opacity: loading ? 0.6 : 1 }} />
      </Card>
    </>
  );
}
