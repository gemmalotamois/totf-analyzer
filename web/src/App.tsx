import { useEffect, useState, type ReactElement } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FightAnalytics, GameListing, SessionRow } from "./types";

const RED = "#e24b3b";
const BLUE = "#3c8fdb";

function clock(seconds: number): string {
  const sign = seconds < 0 ? "−" : "";
  const safe = Math.abs(seconds);
  const mins = Math.floor(safe / 60);
  const secs = safe - mins * 60;
  return `${sign}${mins}:${secs.toFixed(1).padStart(4, "0")}`;
}

function pct(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null, digits = 1): string {
  return value == null ? "—" : value.toFixed(digits);
}

function cornerColor(corner: number): "red" | "blue" {
  return corner === 2 ? "red" : "blue";
}

function seriesPoints(seriesList: FightAnalytics["series"], name: string) {
  const points = new Map<number, { t: number; red: number | null; blue: number | null }>();
  for (const series of seriesList.filter((item) => item.name === name)) {
    const key = cornerColor(series.corner ?? 0);
    series.t.forEach((t, index) => {
      const row = points.get(t) ?? { t, red: null, blue: null };
      row[key] = series.y[index] ?? null;
      points.set(t, row);
    });
  }
  return [...points.values()].sort((a, b) => a.t - b.t);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sessionLabel(name: string): string {
  return name.replace(/^session_/, "").replace(/\.zip$/, "");
}

function formatWhen(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}, ${hours}:${minutes}`;
}

function filenameWhen(name: string): string | null {
  const match = name.match(/_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})\d{2}(?:\.zip)?$/i);
  if (!match) return null;
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${day} ${MONTHS[month - 1]} ${match[1]}, ${match[4]}:${match[5]}`;
}

function RecordingTitle({
  name,
  bout,
  created,
  red,
  blue,
  winner,
  local,
  result,
  notes = [],
}: {
  name: string;
  bout?: string | null;
  created?: string | null;
  red?: string | null;
  blue?: string | null;
  winner?: "red" | "blue" | null;
  local?: "red" | "blue" | null;
  result?: string | null;
  notes?: string[];
}) {
  const when = (created && formatWhen(created)) || filenameWhen(name);
  const subject = local ?? winner ?? (red ? "red" : blue ? "blue" : null);
  const lead = subject === "blue" ? blue : subject === "red" ? red : null;
  const other = subject === "blue" ? red : subject === "red" ? blue : null;
  const tone = result?.startsWith("W") ? "win" : result?.startsWith("L") ? "loss" : result ? "draw" : "";
  const caption = [lead || other ? bout : null, when, ...notes].filter(Boolean).join(" · ");
  return (
    <>
      {lead || other ? (
        <span className="card">
          {lead ? <span className="fighter">{lead}</span> : null}
          {result ? <span className={`badge ${tone}`}>{result}</span> : null}
          {other ? <span className="fighter">{other}</span> : null}
        </span>
      ) : bout ? (
        <span className="bout">{bout}</span>
      ) : (
        sessionLabel(name)
      )}
      {caption ? <small>{caption}</small> : null}
    </>
  );
}

function formatPath(path: string): string {
  return path.replaceAll("/", "/\u200b");
}

async function readError(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    /* response was not JSON */
  }
  return text || "Request failed.";
}

export function App() {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<FightAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reportToken, setReportToken] = useState(0);
  const [importOpen, setImportOpen] = useState(false);
  const [gameListing, setGameListing] = useState<GameListing | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [importing, setImporting] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/sessions")
      .then((response) => response.json())
      .then((rows: SessionRow[]) => {
        setSessions(rows);
        if (rows[0]) setSelected(rows[0].name);
      })
      .catch(() => setError("Could not list recordings. Start totf-analyzer serve."));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setError(null);
    fetch(`/api/sessions/${encodeURIComponent(selected)}/report`)
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      })
      .then((body: FightAnalytics) => setReport(body))
      .catch((exc: Error) => setError(exc.message));
  }, [selected, reportToken]);

  useEffect(() => {
    if (!importOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setImportOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [importOpen]);

  function openImport() {
    setImportOpen(true);
    setImportError(null);
    setGameListing(null);
    fetch("/api/game-recordings")
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json();
      })
      .then((body: GameListing) => setGameListing(body))
      .catch((exc: Error) => setImportError(exc.message));
  }

  async function onImport(name: string) {
    setImporting(name);
    setImportError(null);
    const response = await fetch("/api/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) {
      setImportError(await readError(response));
      setImporting(null);
      return;
    }
    const listed = await fetch("/api/sessions");
    if (listed.ok) setSessions(await listed.json());
    setSelected(name);
    setReportToken((token) => token + 1);
    setImporting(null);
    setImportOpen(false);
  }

  async function onUpload(file: File) {
    const form = new FormData();
    form.append("file", file);
    setError(null);
    const response = await fetch("/api/sessions/upload", { method: "POST", body: form });
    if (!response.ok) {
      setError(await response.text());
      return;
    }
    setReport(await response.json());
    setSelected(file.name);
  }

  return (
    <div className="shell">
      <aside className="rail">
        <h1 className="wordmark">
          TOTF
          <span>ANALYZER</span>
        </h1>
        <label>Recordings</label>
        <ul className="sessions">
          {sessions.map((session) => (
            <li key={session.name}>
              <button
                className={session.name === selected ? "active" : ""}
                onClick={() => setSelected(session.name)}
              >
                <RecordingTitle
                  name={session.name}
                  bout={session.bout}
                  created={session.created}
                  red={session.red}
                  blue={session.blue}
                  winner={session.winner}
                  local={session.local}
                  result={session.result}
                />
              </button>
            </li>
          ))}
        </ul>
        <div className="rail-actions">
          <button type="button" className="upload" onClick={openImport}>
            Import recording
          </button>
          <label className="upload">
            Open a zip
            <input
              type="file"
              accept=".zip"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void onUpload(file);
              }}
            />
          </label>
        </div>
      </aside>
      {importOpen && (
        <div className="import-panel" role="dialog" aria-label="Import recording">
          <div className="import-head">
            <div>
              <h2>Import recording</h2>
              <p className="import-path">{formatPath(gameListing?.directory ?? "Game SessionRecordings folder")}</p>
            </div>
            <button type="button" className="upload" onClick={() => setImportOpen(false)}>
              Close
            </button>
          </div>
          {importError && <p className="error">{importError}</p>}
          {!gameListing && !importError && <p className="empty">Looking for game recordings…</p>}
          {gameListing && !gameListing.available && (
            <p className="empty">The game recordings folder was not found.</p>
          )}
          {gameListing?.available && gameListing.recordings.length === 0 && (
            <p className="empty">No session recordings in that folder.</p>
          )}
          <ul className="import-list">
            {gameListing?.recordings.map((recording) => (
              <li key={recording.name}>
                <button type="button" disabled={importing !== null} onClick={() => void onImport(recording.name)}>
                  <RecordingTitle
                    name={recording.name}
                    bout={recording.bout}
                    created={recording.created}
                    red={recording.red}
                    blue={recording.blue}
                    winner={recording.winner}
                    local={recording.local}
                    result={recording.result}
                    notes={[recording.imported ? "in library" : "", importing === recording.name ? "importing…" : ""]}
                  />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <main className="stage">
        {error && <p className="error">{error}</p>}
        {!report && !error && <p className="empty">Select a session recording.</p>}
        {report && <FightView report={report} />}
      </main>
    </div>
  );
}

function formatDetail(detail: Record<string, string | number | null>): string {
  return Object.entries(detail)
    .filter(([, value]) => value != null && value !== "")
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
    .join(" · ");
}

function fighterMeta(fighter: FightAnalytics["fighters"][number] | undefined, color: string): string {
  const bits = [color, fighter?.stance ?? "stance n/a"];
  if (fighter?.height_m) bits.push(`${fighter.height_m.toFixed(2)} m`);
  if (fighter?.is_local_player) bits.push("local");
  if (fighter?.ai_archetype) bits.push(fighter.ai_archetype);
  if (fighter?.ai_difficulty) bits.push(fighter.ai_difficulty);
  return bits.join(" · ");
}

function FightView({ report }: { report: FightAnalytics }) {
  const red = report.fighters.find((fighter) => fighter.color === "red");
  const blue = report.fighters.find((fighter) => fighter.color === "blue");
  const completedRounds = report.rounds.filter((round) => round.completed);
  const fightKey = report.session.source;
  const [selection, setSelection] = useState({ fightKey, index: completedRounds[0]?.index ?? 0 });
  if (selection.fightKey !== fightKey) {
    setSelection({ fightKey, index: completedRounds[0]?.index ?? 0 });
  }
  const roundIndex = selection.fightKey === fightKey ? selection.index : (completedRounds[0]?.index ?? 0);
  const selected = completedRounds.find((round) => round.index === roundIndex) ?? completedRounds[0];

  return (
    <>
      <section className="scoreboard">
        <div>
          <h2 className="fighter-name red">{red?.name ?? "Red"}</h2>
          <p className="meta">{fighterMeta(red, "red")}</p>
        </div>
        <div className="center-score">
          <div className="method">{report.session.method.replaceAll("_", " ")}</div>
          <strong>
            {report.session.red_total_score ?? "–"}–{report.session.blue_total_score ?? "–"}
          </strong>
          <div className="meta">
            {report.session.venue ?? "venue n/a"} · {clock(report.session.duration_sec)}
          </div>
        </div>
        <div>
          <h2 className="fighter-name blue">{blue?.name ?? "Blue"}</h2>
          <p className="meta right">{fighterMeta(blue, "blue")}</p>
        </div>
      </section>

      {completedRounds.length > 0 && (
        <table className="round-picker">
          <thead>
            <tr>
              <th>Round</th>
              <th>Time</th>
              <th>Red score</th>
              <th>Blue score</th>
              <th>Red hits</th>
              <th>Blue hits</th>
              <th>Red thrown</th>
              <th>Blue thrown</th>
            </tr>
          </thead>
          <tbody>
            {completedRounds.map((round) => {
              const redOff = round.offense.find((item) => item.corner === red?.corner);
              const blueOff = round.offense.find((item) => item.corner === blue?.corner);
              const active = round.index === selected?.index;
              return (
                <tr
                  key={round.index}
                  className={active ? "selected" : undefined}
                  aria-selected={active}
                  tabIndex={0}
                  onClick={() => setSelection({ fightKey, index: round.index })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelection({ fightKey, index: round.index });
                    }
                  }}
                >
                  <td>{round.index + 1}</td>
                  <td className="num">{round.duration_sec == null ? "—" : clock(round.duration_sec)}</td>
                  <td className="num">{round.red.score}</td>
                  <td className="num">{round.blue.score}</td>
                  <td className="num">{round.red.hits}</td>
                  <td className="num">{round.blue.hits}</td>
                  <td className="num">{redOff?.thrown ?? "—"}</td>
                  <td className="num">{blueOff?.thrown ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {selected && <RoundSection key={selected.index} report={report} round={selected} />}

      <details>
        <summary>Measurement notes</summary>
        {report.decode_status.map((note) => (
          <p key={note.field}>
            <span className="conf">{note.confidence}</span> {note.field} — {note.note}
          </p>
        ))}
      </details>
    </>
  );
}

function roundEnding(round: FightAnalytics["rounds"][number]): string | null {
  if (round.red.knocked_out) return "Red KO";
  if (round.blue.knocked_out) return "Blue KO";
  if (round.red.technically_knocked_out) return "Red TKO";
  if (round.blue.technically_knocked_out) return "Blue TKO";
  return null;
}

function RoundSection({
  report,
  round,
}: {
  report: FightAnalytics;
  round: FightAnalytics["rounds"][number];
}) {
  const [momentType, setMomentType] = useState("all");
  const [cornerFilter, setCornerFilter] = useState<number | "both">("both");
  const red = report.fighters.find((fighter) => fighter.color === "red");
  const blue = report.fighters.find((fighter) => fighter.color === "blue");
  const redOff = round.offense.find((item) => item.corner === red?.corner);
  const blueOff = round.offense.find((item) => item.corner === blue?.corner);
  const redDef = round.defense.find((item) => item.corner === red?.corner);
  const blueDef = round.defense.find((item) => item.corner === blue?.corner);
  const redGuard = redDef?.guard_discipline;
  const blueGuard = blueDef?.guard_discipline;
  const redFoot = redDef?.footwork;
  const blueFoot = blueDef?.footwork;
  const ending = roundEnding(round);
  const hands = handChart(round.offense);
  const punishment = seriesPoints(round.series, "punishment_0");
  const guard = seriesPoints(round.series, "guard_up").map((point) => ({
    ...point,
    red: point.red == null ? null : point.red * 100,
    blue: point.blue == null ? null : point.blue * 100,
  }));
  const head = seriesPoints(round.series, "head_speed");
  const density = seriesPoints(round.series, "punch_density");
  const momentTypes = ["all", ...new Set(round.key_moments.map((moment) => moment.type))];
  const moments = round.key_moments.filter((moment) => momentType === "all" || moment.type === momentType);
  const punches = round.offense
    .filter((offense) => cornerFilter === "both" || offense.corner === cornerFilter)
    .flatMap((offense) => offense.punches.map((punch) => ({ ...punch, corner: offense.corner })));

  return (
    <section className="round">
      <header className="round-head">
        <h2>Round {round.index + 1}</h2>
        <strong>
          {round.red.score}–{round.blue.score}
        </strong>
        {round.duration_sec != null && <span className="meta">{clock(round.duration_sec)}</span>}
        {ending && <span className="method">{ending}</span>}
      </header>
      {round.offense.length === 0 ? (
        <p className="empty">This round has a scorecard, and the round clock was not found in the recording.</p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>Measure</th>
                <th>Red</th>
                <th>Blue</th>
              </tr>
            </thead>
            <tbody>
              <Row label="Hits taken" red={round.red.hits} blue={round.blue.hits} />
              <Row label="Thrown records" red={redOff?.thrown} blue={blueOff?.thrown} />
              <Row label="With speed data" red={redOff?.kinematic_thrown} blue={blueOff?.kinematic_thrown} />
              <Row label="Landed" red={redOff?.landed} blue={blueOff?.landed} />
              <Row label="Accuracy" red={pct(redOff?.accuracy ?? null)} blue={pct(blueOff?.accuracy ?? null)} />
              <Row label="Punches / min" red={num(redOff?.punches_per_minute ?? null)} blue={num(blueOff?.punches_per_minute ?? null)} />
              <Row label="Mean speed" red={num(redOff?.velocity.mean_thrown ?? null, 2)} blue={num(blueOff?.velocity.mean_thrown ?? null, 2)} />
              <Row label="Mean speed, landed" red={num(redOff?.velocity.mean_landed ?? null, 2)} blue={num(blueOff?.velocity.mean_landed ?? null, 2)} />
              <Row label="Official damage taken" red={round.red.received_damage} blue={round.blue.received_damage} />
              <Row label="Stuns" red={round.red.stuns} blue={round.blue.stuns} />
              <Row label="Knockdowns" red={round.red.all_knockdowns} blue={round.blue.all_knockdowns} />
              <Row label="Guard up" red={num(redDef?.guard_up_percent ?? null)} blue={num(blueDef?.guard_up_percent ?? null)} suffix="%" />
              <Row label="Guard up under fire" red={num(redDef?.guard_up_percent_under_fire ?? null)} blue={num(blueDef?.guard_up_percent_under_fire ?? null)} suffix="%" />
              <Row label="Left glove up" red={num(redGuard?.left_guard_up_percent ?? null)} blue={num(blueGuard?.left_guard_up_percent ?? null)} suffix="%" />
              <Row label="Right glove up" red={num(redGuard?.right_guard_up_percent ?? null)} blue={num(blueGuard?.right_guard_up_percent ?? null)} suffix="%" />
              <Row label="Either glove up" red={num(redGuard?.either_guard_up_percent ?? null)} blue={num(blueGuard?.either_guard_up_percent ?? null)} suffix="%" />
              <Row label="Off-hand kept" red={num(redGuard?.offhand_guard_kept_percent ?? null)} blue={num(blueGuard?.offhand_guard_kept_percent ?? null)} suffix="%" />
              <Row label="Off-hand kept, throwing left" red={num(redGuard?.offhand_guard_kept_percent_throwing_left ?? null)} blue={num(blueGuard?.offhand_guard_kept_percent_throwing_left ?? null)} suffix="%" />
              <Row label="Off-hand kept, throwing right" red={num(redGuard?.offhand_guard_kept_percent_throwing_right ?? null)} blue={num(blueGuard?.offhand_guard_kept_percent_throwing_right ?? null)} suffix="%" />
              <Row label="Hand returned" red={num(redGuard?.retraction_returned_percent ?? null)} blue={num(blueGuard?.retraction_returned_percent ?? null)} suffix="%" />
              <Row label="Return time, median" red={num(redGuard?.retraction_time_ms.median ?? null, 0)} blue={num(blueGuard?.retraction_time_ms.median ?? null, 0)} suffix=" ms" />
              <Row label="Return droop, median" red={num(redGuard?.retraction_droop_m.median ?? null, 2)} blue={num(blueGuard?.retraction_droop_m.median ?? null, 2)} suffix=" m" />
              <Row label="Under fire" red={num(redDef?.under_fire_seconds ?? null)} blue={num(blueDef?.under_fire_seconds ?? null)} suffix=" s" />
              <Row label="Head speed" red={num(redDef?.head_mean_speed ?? null, 2)} blue={num(blueDef?.head_mean_speed ?? null, 2)} />
              <Row label="Head speed under fire" red={num(redDef?.head_mean_speed_under_fire ?? null, 2)} blue={num(blueDef?.head_mean_speed_under_fire ?? null, 2)} />
              <Row label="Head height range" red={num(redDef?.head_vertical_range ?? null, 2)} blue={num(blueDef?.head_vertical_range ?? null, 2)} suffix=" m" />
              <Row label="Radial distance" red={num(redFoot?.radial_distance_m ?? null, 1)} blue={num(blueFoot?.radial_distance_m ?? null, 1)} suffix=" m" />
              <Row label="Lateral distance" red={num(redFoot?.tangential_distance_m ?? null, 1)} blue={num(blueFoot?.tangential_distance_m ?? null, 1)} suffix=" m" />
              <Row label="Lateral share" red={pct(redFoot?.tangential_share ?? null)} blue={pct(blueFoot?.tangential_share ?? null)} />
              <Row label="Clockwise" red={num(redFoot?.clockwise_distance_m ?? null, 1)} blue={num(blueFoot?.clockwise_distance_m ?? null, 1)} suffix=" m" />
              <Row label="Counterclockwise" red={num(redFoot?.counterclockwise_distance_m ?? null, 1)} blue={num(blueFoot?.counterclockwise_distance_m ?? null, 1)} suffix=" m" />
              <Row label="Angle cuts" red={redFoot?.angle_cut_count ?? null} blue={blueFoot?.angle_cut_count ?? null} />
              <Row label="Entry angle" red={num(redFoot?.attack_entry_angle_deg ?? null, 1)} blue={num(blueFoot?.attack_entry_angle_deg ?? null, 1)} suffix="°" />
              <Row label="Exit angle" red={num(redFoot?.post_combo_exit_angle_deg ?? null, 1)} blue={num(blueFoot?.post_combo_exit_angle_deg ?? null, 1)} suffix="°" />
              <Row label="Range, mean" red={num(redFoot?.fighter_distance_m.mean ?? null, 2)} blue={num(blueFoot?.fighter_distance_m.mean ?? null, 2)} suffix=" m" />
              <Row label="Clinch range" red={num(redFoot?.clinch_range_percent ?? null)} blue={num(blueFoot?.clinch_range_percent ?? null)} suffix="%" />
              <Row label="Pocket range" red={num(redFoot?.pocket_range_percent ?? null)} blue={num(blueFoot?.pocket_range_percent ?? null)} suffix="%" />
              <Row label="Outside range" red={num(redFoot?.outside_range_percent ?? null)} blue={num(blueFoot?.outside_range_percent ?? null)} suffix="%" />
              <Row label="Shoulder blade" red={num(redFoot?.shoulder_blade_angle_deg ?? null, 1)} blue={num(blueFoot?.shoulder_blade_angle_deg ?? null, 1)} suffix="°" />
              <Row label="Foot blade" red={num(redFoot?.foot_blade_angle_deg ?? null, 1)} blue={num(blueFoot?.foot_blade_angle_deg ?? null, 1)} suffix="°" />
              <Row label="Square stance" red={num(redFoot?.square_stance_percent ?? null)} blue={num(blueFoot?.square_stance_percent ?? null)} suffix="%" />
              <Row label="Lead left" red={num(redFoot?.lead_left_percent ?? null)} blue={num(blueFoot?.lead_left_percent ?? null)} suffix="%" />
              <Row label="Square while punching" red={num(redFoot?.square_while_punching_percent ?? null)} blue={num(blueFoot?.square_while_punching_percent ?? null)} suffix="%" />
            </tbody>
          </table>

          <div className="charts">
            <div className="chart-block">
              <h3>Punches by hand</h3>
              <ChartFrame>
                <BarChart data={hands}>
                  <CartesianGrid stroke="#34373e" vertical={false} />
                  <XAxis dataKey="name" stroke="#9b9488" interval={0} />
                  <YAxis stroke="#9b9488" />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="thrown" fill="#9b9488" />
                  <Bar dataKey="landed" fill="#e0a23a" />
                </BarChart>
              </ChartFrame>
            </div>
            <div className="chart-block">
              <h3>Punch density</h3>
              <ChartFrame>
                <LineChart data={density}>
                  <CartesianGrid stroke="#34373e" vertical={false} />
                  <XAxis dataKey="t" stroke="#9b9488" tickFormatter={(value) => clock(Number(value))} />
                  <YAxis stroke="#9b9488" />
                  <Tooltip labelFormatter={(value) => clock(Number(value))} />
                  <Legend />
                  <Line dataKey="red" name="red" stroke={RED} dot={false} strokeWidth={1.6} />
                  <Line dataKey="blue" name="blue" stroke={BLUE} dot={false} strokeWidth={1.6} />
                </LineChart>
              </ChartFrame>
            </div>
            <div className="chart-block">
              <h3>Punishment meter 0</h3>
              <ChartFrame>
                <LineChart data={punishment}>
                  <CartesianGrid stroke="#34373e" vertical={false} />
                  <XAxis dataKey="t" stroke="#9b9488" tickFormatter={(value) => clock(Number(value))} />
                  <YAxis stroke="#9b9488" />
                  <Tooltip labelFormatter={(value) => clock(Number(value))} />
                  <Legend />
                  <Line dataKey="red" name="red" stroke={RED} dot={false} strokeWidth={1.6} />
                  <Line dataKey="blue" name="blue" stroke={BLUE} dot={false} strokeWidth={1.6} />
                </LineChart>
              </ChartFrame>
            </div>
            <div className="chart-block">
              <h3>Guard up</h3>
              <ChartFrame>
                <LineChart data={guard}>
                  <CartesianGrid stroke="#34373e" vertical={false} />
                  <XAxis dataKey="t" stroke="#9b9488" tickFormatter={(value) => clock(Number(value))} />
                  <YAxis stroke="#9b9488" unit="%" />
                  <Tooltip labelFormatter={(value) => clock(Number(value))} />
                  <Legend />
                  <Line dataKey="red" name="red" stroke={RED} dot={false} strokeWidth={1.6} />
                  <Line dataKey="blue" name="blue" stroke={BLUE} dot={false} strokeWidth={1.6} />
                </LineChart>
              </ChartFrame>
            </div>
            <div className="chart-block">
              <h3>Head speed</h3>
              <ChartFrame>
                <LineChart data={head}>
                  <CartesianGrid stroke="#34373e" vertical={false} />
                  <XAxis dataKey="t" stroke="#9b9488" tickFormatter={(value) => clock(Number(value))} />
                  <YAxis stroke="#9b9488" />
                  <Tooltip labelFormatter={(value) => clock(Number(value))} />
                  <Legend />
                  <Line dataKey="red" name="red" stroke={RED} dot={false} strokeWidth={1.6} />
                  <Line dataKey="blue" name="blue" stroke={BLUE} dot={false} strokeWidth={1.6} />
                </LineChart>
              </ChartFrame>
            </div>
            <TrajectoryTable offense={round.offense} />
          </div>

          <h3 className="round-sub">Key moments</h3>
          <div className="filters">
            {momentTypes.map((type) => (
              <button key={type} className={momentType === type ? "on" : ""} onClick={() => setMomentType(type)}>
                {type.replaceAll("_", " ")}
              </button>
            ))}
          </div>
          <ul className="moments">
            {moments.map((moment) => (
              <li key={`${moment.tick}-${moment.type}-${moment.corners.join()}`}>
                <span className="time">{clock(moment.t_sec)}</span>
                <span className={`tag ${moment.corners.length === 1 ? cornerColor(moment.corners[0]) : ""}`}>
                  {moment.type.replaceAll("_", " ")}
                </span>
                <span>{formatDetail(moment.detail)}</span>
              </li>
            ))}
          </ul>

          <h3 className="round-sub">Punch log</h3>
          <div className="filters">
            {(["both", 2, 1] as const).map((corner) => (
              <button key={String(corner)} className={cornerFilter === corner ? "on" : ""} onClick={() => setCornerFilter(corner)}>
                {corner === "both" ? "Both" : cornerColor(corner)}
              </button>
            ))}
          </div>
          <div className="log">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Corner</th>
                  <th>Hand</th>
                  <th>Trajectory</th>
                  <th>Speed</th>
                  <th>Landed</th>
                  <th>Punishment step</th>
                </tr>
              </thead>
              <tbody>
                {punches.map((punch, index) => (
                  <tr key={`${punch.t_sec}-${punch.corner}-${index}`}>
                    <td>{clock(punch.t_sec)}</td>
                    <td className={`tag ${cornerColor(punch.corner)}`}>{cornerColor(punch.corner)}</td>
                    <td>{punch.hand.replaceAll("_", " ")}</td>
                    <td>{punch.trajectory}</td>
                    <td className="num">{num(punch.speed, 2)}</td>
                    <td className={punch.landed ? "landed" : ""}>{punch.landed ? "yes" : "no"}</td>
                    <td className="num">{num(punch.punishment_delta, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function Row({
  label,
  red,
  blue,
  suffix = "",
}: {
  label: string;
  red: string | number | null | undefined;
  blue: string | number | null | undefined;
  suffix?: string;
}) {
  const cell = (value: string | number | null | undefined) => (value == null || value === "" ? "—" : `${value}${suffix}`);
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{cell(red)}</td>
      <td className="num">{cell(blue)}</td>
    </tr>
  );
}

function ChartFrame({ children }: { children: ReactElement }) {
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

function handChart(offenseRows: FightAnalytics["offense"]) {
  const points = [];
  for (const offense of offenseRows) {
    const color = cornerColor(offense.corner);
    for (const hand of offense.hands) {
      if (hand.hand === "unmatched") continue;
      points.push({
        name: `${color === "red" ? "R" : "B"} ${hand.hand.replace("hand_", "")}`,
        thrown: hand.thrown,
        landed: hand.landed,
      });
    }
  }
  return points;
}

function TrajectoryTable({ offense }: { offense: FightAnalytics["offense"] }) {
  const names = Array.from(
    new Set(offense.flatMap((item) => item.trajectories.map((row) => row.trajectory))),
  );
  return (
    <div className="chart-block span">
      <h3>Trajectory</h3>
      <table>
        <thead>
          <tr>
            <th>Type</th>
            <th>Red thrown</th>
            <th>Red landed</th>
            <th>Blue thrown</th>
            <th>Blue landed</th>
          </tr>
        </thead>
        <tbody>
          {names.map((name) => {
            const red = offense.find((item) => cornerColor(item.corner) === "red")?.trajectories.find((row) => row.trajectory === name);
            const blue = offense.find((item) => cornerColor(item.corner) === "blue")?.trajectories.find((row) => row.trajectory === name);
            return (
              <tr key={name}>
                <td>{name}</td>
                <td className="num">{red?.thrown ?? 0}</td>
                <td className="num">{red?.landed ?? 0}</td>
                <td className="num">{blue?.thrown ?? 0}</td>
                <td className="num">{blue?.landed ?? 0}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
