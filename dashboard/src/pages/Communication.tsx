import { useApi, type Language } from "../api";
import { ColumnChart, LineChart, series, token } from "../chart";
import { AgentLink, Card, Status, Tile } from "../components";

// Speech acts folded into at most 8 groups (fixed colour order, never cycled).
const ACT_GROUPS: [string, string[]][] = [
  ["chat", ["chat"]],
  ["greet", ["greet"]],
  ["tell where", ["share_info"]],
  ["give", ["give"]],
  ["ask or order", ["request", "order"]],
  ["answer", ["reply"]],
  ["warmth", ["praise", "gossip", "propose", "found", "invite"]],
  ["hostility", ["insult", "threaten", "attack", "challenge"]],
];

export default function Communication() {
  const { data, error, loading } = useApi<Language>("/api/language");
  if (!data) return <Status error={error} loading={loading} />;
  const s = data.series;
  const w = data.words;
  const line = (key: keyof (typeof s)[number]) => s.map((d) => [d.day, d[key] as number] as [number, number]);
  const last = s[s.length - 1];
  const first7 = s.slice(0, 7), last7 = s.slice(-7);
  const avg = (xs: typeof s, k: "wordsPerLine" | "voicedShare" | "turnsPerConversation") =>
    xs.length ? xs.reduce((a, d) => a + d[k], 0) / xs.length : 0;
  const answers = (k: string) => s.map((d) => [d.day, d.answers[k] ?? 0] as [number, number]);
  const names = data.names;
  return (
    <>
      <h1>Communication</h1>
      <p className="sub">
        How the agents talk, and how it changes: every message, measured per simulated day (the x-axis of
        every chart). Words come from lines spoken in their own words (the LLM mind); rule-made acts have no words.
      </p>
      <div className="grid tiles">
        <Tile label="Vocabulary" value={w.vocabulary.toLocaleString()} note={`${w.totalWords.toLocaleString()} words spoken`} />
        <Tile label="New words, last day" value={last?.newWords ?? 0} note={`day ${last?.day ?? "–"}`} />
        <Tile label="Words per line" value={avg(last7, "wordsPerLine").toFixed(1)}
          note={`first week ${avg(first7, "wordsPerLine").toFixed(1)}`} />
        <Tile label="Turns per conversation" value={avg(last7, "turnsPerConversation").toFixed(1)}
          note={`first week ${avg(first7, "turnsPerConversation").toFixed(1)}`} />
        <Tile label="Valley words" value={w.dictionary ? w.valley.length : "–"}
          note={w.dictionary ? "not English, not a name" : "no word list on this machine"} />
      </div>

      <div className="grid three" style={{ marginTop: 14 }}>
        <Card title="Vocabulary size" hint="distinct words ever spoken">
          <LineChart loading={loading} lines={[{ name: "words known", color: series(1), area: true, data: line("vocabulary") }]} />
        </Card>
        <Card title="New words per day" hint="first time anyone said them">
          <ColumnChart loading={loading} bars={[{ name: "new words", color: series(1), data: line("newWords") }]} />
        </Card>
        <Card title="Words per spoken line">
          <LineChart loading={loading} lines={[{ name: "words per line", color: series(1), data: line("wordsPerLine") }]} />
        </Card>
        <Card title="Messages and conversations per day">
          <LineChart loading={loading} lines={[
            { name: "messages", color: series(1), data: line("messages") },
            { name: "conversations", color: series(2), data: line("conversations") },
          ]} />
        </Card>
        <Card title="Who talks with whom" hint="per day">
          <LineChart loading={loading} lines={[
            { name: "pairs talking", color: series(1), data: line("pairs") },
            { name: "people speaking", color: series(2), data: line("speakers") },
          ]} />
        </Card>
        <Card title="Turns per conversation">
          <LineChart loading={loading} lines={[{ name: "turns", color: series(1), data: line("turnsPerConversation") }]} />
        </Card>
        <Card title="In their own words" hint="share of messages with a spoken line">
          <LineChart loading={loading} percent yMin={0} yMax={1}
            lines={[{ name: "own words", color: series(1), area: true, data: line("voicedShare") }]} />
        </Card>
        <Card title="Lies" hint="share of messages that were lies">
          <LineChart loading={loading} percent yMin={0}
            lines={[{ name: "lies", color: token("--lie"), data: line("lieRate") }]} />
        </Card>
        <Card title="Answers" hint="to requests, threats and proposals">
          <LineChart loading={loading} lines={[
            { name: "refused to share", color: series(1), data: answers("no") },
            { name: "stood up to a threat", color: series(2), data: answers("refuse") },
            { name: "agreed to a child", color: series(3), data: answers("agree") },
            { name: "declined a child", color: series(4), data: answers("decline") },
          ]} />
        </Card>
      </div>

      <div style={{ marginTop: 14 }}>
        <Card title="What they talk about" hint="kinds of speech act per day">
          <ColumnChart stacked loading={loading} height={260} bars={ACT_GROUPS.map(([name, acts], i) => ({
            name, color: series(i + 1),
            data: s.map((d) => [d.day, acts.reduce((a, k) => a + (d.acts[k] ?? 0), 0)] as [number, number]),
          }))} />
        </Card>
      </div>

      <div className="grid two" style={{ marginTop: 14 }}>
        <Card title="Valley words" hint="not in this machine's English word list and not anyone's name: coined, misspelled, or English the list lacks">
          {!w.dictionary ? <p className="dim">No system word list found (/usr/share/dict).</p> :
            w.valley.length === 0 ? <p className="dim">None yet: everything said so far is English.</p> : (
              <table className="data">
                <thead><tr><th>Word</th><th className="num">Uses</th><th>First said</th></tr></thead>
                <tbody>{w.valley.slice(0, 25).map((u) => (
                  <tr key={u.word}><td><b>{u.word}</b></td><td className="num">{u.count}</td>
                    <td>day {u.day} by <AgentLink id={u.speaker} name={names[u.speaker] ?? "?"} />:{" "}
                      <span className="ink2">“{u.line}”</span></td></tr>
                ))}</tbody>
              </table>
            )}
        </Card>
        <Card title="Rising words" hint="used much more in the last 7 days than before">
          <div className="words">
            {w.rising.map((r) => <span key={r.word} className="word"><b>{r.word}</b><i>×{r.ratio}</i></span>)}
            {!w.rising.length && <span className="dim">Not enough days yet.</span>}
          </div>
          <h2 style={{ marginTop: 16 }}>Most used words</h2>
          <div className="words">
            {w.top.map((t) => <span key={t.word} className="word">{t.word}<i>{t.count}</i></span>)}
          </div>
        </Card>
      </div>
      <div style={{ marginTop: 14 }}>
        <Card title="Newest words" hint="most recent first use of each word">
          <table className="data">
            <thead><tr><th>Word</th><th>When</th><th>Said by</th><th>In</th></tr></thead>
            <tbody>{w.newest.slice(0, 20).map((u) => (
              <tr key={u.word}><td><b>{u.word}</b></td><td>day {u.day}</td>
                <td><AgentLink id={u.speaker} name={names[u.speaker] ?? "?"} /></td>
                <td className="ink2">“{u.line}”</td></tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    </>
  );
}
