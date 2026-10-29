import { useEffect, useState } from "react";
import { LiveContext, useApi, useLiveTick, useRoute, type Overview as O } from "./api";
import { WhyProvider } from "./components";
import Agent from "./pages/Agent";
import Agents from "./pages/Agents";
import Communication from "./pages/Communication";
import Conversations from "./pages/Conversations";
import Economy from "./pages/Economy";
import Events from "./pages/Events";
import Family from "./pages/Family";
import God from "./pages/God";
import WorldMap from "./pages/Map";
import MindLab from "./pages/MindLab";
import Compare from "./pages/Compare";
import Goals from "./pages/Goals";
import Groups from "./pages/Groups";
import History from "./pages/History";
import Ideas from "./pages/Ideas";
import Llm from "./pages/Llm";
import Network from "./pages/Network";
import Overview from "./pages/Overview";
import Pair from "./pages/Pair";
import Run from "./pages/Run";

const NAV: [string, string][] = [
  ["", "Overview"], ["map", "World map"], ["conversations", "Conversations"], ["communication", "Communication"],
  ["agents", "Agents"], ["groups", "Groups"], ["ideas", "Ideas"], ["goals", "Goals"], ["economy", "Economy"], ["history", "History"], ["family", "Family"], ["mindlab", "Mind lab"], ["compare", "Compare"], ["god", "God Mode"], ["run", "Run"], ["network", "Network"], ["llm", "LLM monitor"],
  ["events", "Events"],
];

function loadTheme(): string {
  try { return localStorage.getItem("genesis-theme") ?? "auto"; } catch { return "auto"; }
}

export default function App() {
  const { path, params } = useRoute();
  const liveTick = useLiveTick();
  const [live, setLive] = useState(true);
  const [theme, setTheme] = useState(loadTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("genesis-theme", theme); } catch { /* private window */ }
  }, [theme]);
  const page = path[0] ?? "";
  const top = page === "agent" || page === "pair" ? (page === "agent" ? "agents" : "conversations") : page;

  let body;
  switch (page) {
    case "conversations": body = <Conversations params={params} />; break;
    case "communication": body = <Communication />; break;
    case "agents": body = <Agents />; break;
    case "agent": body = <Agent key={path[1]} id={Number(path[1])} tab={path[2]} params={params} />; break;
    case "pair": body = <Pair a={Number(path[1])} b={Number(path[2])} params={params} />; break;
    case "llm": body = <Llm />; break;
    case "groups": body = <Groups />; break;
    case "ideas": body = <Ideas />; break;
    case "goals": body = <Goals />; break;
    case "economy": body = <Economy />; break;
    case "history": body = <History />; break;
    case "family": body = <Family />; break;
    case "run": body = <Run />; break;
    case "map": body = <WorldMap />; break;
    case "mindlab": body = <MindLab />; break;
    case "compare": body = <Compare />; break;
    case "god": body = <God />; break;
    case "network": body = <Network />; break;
    case "events": body = <Events params={params} />; break;
    default: body = <Overview />;
  }
  return (
    <LiveContext.Provider value={{ tick: liveTick.tick, live }}>
      <WhyProvider>
        <div className="layout">
          <nav className="side">
            <div className="brand">GENESIS</div>
            <WorldName />
            {NAV.map(([href, label]) => (
              <a key={href} href={`#/${href}`} className={top === href ? "active" : ""}>{label}</a>
            ))}
            <div className="live">
              <div>{liveTick.connected ? "● connected" : "○ reconnecting…"}</div>
              <div>{liveTick.clock}</div>
              <label style={{ display: "flex", gap: 6, marginTop: 6 }}>
                <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> live updates
              </label>
              <label style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center" }}>
                theme <select value={theme} onChange={(e) => setTheme(e.target.value)}>
                  <option value="auto">auto</option><option value="dark">dark</option><option value="light">light</option>
                </select>
              </label>
            </div>
          </nav>
          <main>{body}</main>
        </div>
      </WhyProvider>
    </LiveContext.Provider>
  );
}

function WorldName() {
  const { data } = useApi<O>("/api/overview", false);
  return <div className="world">{data ? `${data.world} · seed ${data.seed}` : "…"}</div>;
}
