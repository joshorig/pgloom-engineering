export function formatMicros(value?: number | null, precision?: number): string {
  if (value == null) return "-";
  const dollars = value / 1_000_000;
  const digits = precision ?? (Math.abs(dollars) < 0.1 ? 4 : Math.abs(dollars) < 10 ? 3 : 2);
  return `$${dollars.toFixed(digits)}`;
}

export function formatTokens(value?: number | null): string {
  if (value == null) return "-";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export function formatSeconds(value?: number | null): string {
  if (value == null) return "-";
  if (value >= 3600) return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
  if (value >= 60) return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
  return `${value.toFixed(1)}s`;
}
