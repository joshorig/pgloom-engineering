import { Search, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useApi, type FeatureRow } from "../api";
import { CostCell, StatusPill } from "../components/primitives";
import { formatMicros } from "../lib/money";

type SortKey = "feature_id" | "project" | "branch" | "state" | "abort_reason" | "runs" | "cost" | "roles" | "blocker" | "created_at";
type SortDir = "asc" | "desc" | null;

const sortable: Array<{ key: SortKey; label: string; align?: "right"; width?: string }> = [
  { key: "feature_id", label: "feature_id", width: "18%" },
  { key: "project", label: "project", width: "12%" },
  { key: "branch", label: "branch", width: "16%" },
  { key: "state", label: "state", width: "92px" },
  { key: "abort_reason", label: "abort_reason", width: "130px" },
  { key: "runs", label: "runs", align: "right", width: "70px" },
  { key: "cost", label: "cost", align: "right", width: "88px" },
  { key: "roles", label: "roles", width: "92px" },
  { key: "blocker", label: "blocker", width: "20%" },
  { key: "created_at", label: "created", align: "right", width: "150px" }
];

export function FeaturesList() {
  const { data, error, isLoading } = useApi<FeatureRow[]>("/api/features");
  const rows = data || [];
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [showAbortReason, setShowAbortReason] = useState(false);
  const visibleColumns = useMemo(
    () => sortable.filter((col) => showAbortReason || col.key !== "abort_reason"),
    [showAbortReason]
  );

  const filteredRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? rows.filter((row) => [
        row.feature_id || row.id,
        row.project,
        row.branch,
        row.state,
        row.abort_reason,
        row.roles_seen,
        row.last_blocker
      ].some((value) => String(value || "").toLowerCase().includes(needle)))
      : rows;
    if (!sortDir) return filtered;
    return [...filtered].sort((a, b) => compareRows(a, b, sortKey, sortDir));
  }, [query, rows, sortDir, sortKey]);

  const totals = useMemo(() => filteredRows.reduce((acc, row) => ({
    runs: acc.runs + Number(row.runs || 0),
    cost: acc.cost + Number(row.cost_usd_micros || 0)
  }), { runs: 0, cost: 0 }), [filteredRows]);

  const setSort = (key: SortKey) => {
    if (key !== sortKey) {
      setSortKey(key);
      setSortDir("asc");
      return;
    }
    setSortDir((dir) => dir === "asc" ? "desc" : dir === "desc" ? null : "asc");
  };

  return (
    <div className="cc-pane">
      <div className="cc-flist-bar">
        <div className="cc-flist-title">
          <span className="cc-kicker mono">FEATURES</span>
          <span className="num cc-dim">{rows.length} active</span>
        </div>
        <div className="cc-flist-tools">
          <input
            className="cc-input cc-flist-filter"
            placeholder="filter feature_id, project, branch..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span className="cc-chip is-on">state:any<span className="cc-chip-x">×</span></span>
          <span className="cc-chip">role:any</span>
          <button className={`cc-chip ${showAbortReason ? "is-on" : ""}`} onClick={() => setShowAbortReason((value) => !value)} type="button">abort_reason</button>
          <button className="cc-btn cc-btn-ghost" title="Search command center">
            <Search size={13} />
            <span className="mono cc-dim">⌘K</span>
          </button>
          <button className="cc-btn cc-btn-primary">
            <Plus size={13} />
            New feature
          </button>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 14px", borderBottom: "1px solid var(--line)", background: "var(--panel)" }}>
        <span className="cc-kicker mono">OPERATOR SURFACES</span>
        <a className="cc-btn cc-btn-ghost" href="/realtime">Realtime</a>
        <a className="cc-btn cc-btn-ghost" href="/telemetry/slots">Slot occupancy</a>
        <a className="cc-btn cc-btn-ghost" href="/telemetry/tokens">Token economy</a>
      </div>
      <div className="cc-flist-tbl">
        {isLoading && <div className="cc-state-title" style={{ padding: 16 }}>Loading features...</div>}
        {error && <div className="cc-state cc-state-fail" style={{ margin: 16 }}>Failed to load features</div>}
        {!isLoading && !error && filteredRows.length === 0 && <div className="cc-state" style={{ margin: 16 }}><div className="cc-state-title">No matching features</div><div className="cc-state-desc">Command Center will populate as engineering features are registered.</div></div>}
        {filteredRows.length > 0 && (
          <table className="cc-table cc-features-table">
            <thead>
              <tr>
                <th style={{ width: 28 }}></th>
                {visibleColumns.map((col) => (
                  <th key={col.key} style={{ width: col.width, textAlign: col.align }}>
                    <button className="cc-sort" onClick={() => setSort(col.key)} type="button">
                      <span>{col.label}</span>
                      <span className={`cc-sort-glyph ${sortKey === col.key && sortDir ? "is-on" : ""}`}>{sortKey === col.key && sortDir === "desc" ? "⌄" : "⌃"}</span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row, idx) => {
                const id = row.feature_id || row.id || "";
                return (
                  <tr key={id} onClick={() => { window.location.href = `/feature/${id}`; }} className={idx === 0 ? "is-selected" : ""}>
                    <td className="cc-dim mono cc-row-caret">{idx === 0 ? "▸" : ""}</td>
                    <td className="mono cc-ellipsis" title={id}>{id}</td>
                    <td className="mono cc-dim cc-ellipsis" title={row.project}>{row.project}</td>
                    <td className="mono cc-dim cc-ellipsis" title={row.branch || "-"}>{row.branch || "-"}</td>
                    <td><StatusPill status={row.paused ? "paused" : row.state} /></td>
                    {showAbortReason && <td className="mono cc-dim cc-ellipsis" title={row.abort_reason || "-"}>{row.abort_reason ? <StatusPill status="blocked" label={row.abort_reason} /> : "-"}</td>}
                    <td className="num" style={{ textAlign: "right" }}>{row.runs || 0}</td>
                    <td style={{ textAlign: "right" }}><CostCell micros={row.cost_usd_micros} precision={2} /></td>
                    <td><RoleBars rolesSeen={row.roles_seen} /></td>
                    <td className="mono cc-dim cc-ellipsis" title={row.last_blocker || "-"}>{row.last_blocker || "-"}</td>
                    <td className="mono cc-dim" style={{ textAlign: "right" }}>{formatDate(row.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="cc-flist-foot mono cc-dim">
        <span>Σ {filteredRows.length} rows</span>
        <span>·</span>
        <span>{totals.runs} total runs</span>
        <span>·</span>
        <span>{formatMicros(totals.cost, 2)} accumulated</span>
        <span className="cc-flist-foot-live">last NOTIFY live · cc_events · websocket</span>
      </div>
    </div>
  );
}

function RoleBars({ rolesSeen }: { rolesSeen?: string | null }) {
  const roles = rolesSeen?.split(",").map((role) => role.trim()).filter(Boolean) || [];
  return (
    <span className="cc-role-bars" title={roles.length ? roles.join(", ") : "no roles observed"}>
      {Array.from({ length: 6 }).map((_, idx) => <i key={idx} className={idx < Math.min(roles.length, 6) ? "is-on" : ""} />)}
    </span>
  );
}

function compareRows(a: FeatureRow, b: FeatureRow, key: SortKey, dir: Exclude<SortDir, null>) {
  const mod = dir === "asc" ? 1 : -1;
  const av = valueForSort(a, key);
  const bv = valueForSort(b, key);
  if (typeof av === "number" && typeof bv === "number") return (av - bv) * mod;
  return String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" }) * mod;
}

function valueForSort(row: FeatureRow, key: SortKey) {
  switch (key) {
    case "feature_id": return row.feature_id || row.id || "";
    case "cost": return Number(row.cost_usd_micros || 0);
    case "runs": return Number(row.runs || 0);
    case "roles": return row.roles_seen || "";
    case "blocker": return row.last_blocker || "";
    case "abort_reason": return row.abort_reason || "";
    case "created_at": return Date.parse(row.created_at || "") || 0;
    default: return row[key] || "";
  }
}

function formatDate(value?: string | null) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toISOString().slice(0, 16).replace("T", " ");
}
