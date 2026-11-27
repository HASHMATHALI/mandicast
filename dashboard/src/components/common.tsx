// Small shared pieces: notices (icon + label, never colour alone), query states, source attribution, crop chips.
import type { ReactNode } from "react";
import { errorMessage } from "../api/client";
import type { Commodity, Source } from "../api/types";
import { formatDate, formatDateTime } from "../lib/format";
import { t } from "../strings";

type Tone = "info" | "warning" | "error" | "ok";
const ICONS: Record<Tone, string> = { info: "i", warning: "!", error: "×", ok: "✓" };
const LABELS: Record<Tone, string> = { info: "Note", warning: "Warning", error: "Error", ok: "Done" };

export function Notice({ tone = "info", title, children }: { tone?: Tone; title?: string; children?: ReactNode }) {
  return (
    <div className={`notice ${tone}`} role={tone === "error" ? "alert" : undefined}>
      <span className="icon" aria-hidden="true">{ICONS[tone]}</span>
      <div>
        <span className="visually-hidden">{LABELS[tone]}: </span>
        {title && <p><strong>{title}</strong></p>}
        {children}
      </div>
    </div>
  );
}

export function Loading({ label = t.loading }: { label?: string }) {
  return <p className="muted" role="status">{label}</p>;
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Notice tone="error">
      <p>{errorMessage(error)}</p>
      {onRetry && <button type="button" onClick={onRetry}>{t.retry}</button>}
    </Notice>
  );
}

export function SourceNote({ source, asOf }: {
  source?: Source; asOf?: { latest_arrival_date: string | null; last_successful_ingestion_at: string | null };
}) {
  if (!source) return null;
  return (
    <p className="source-note">
      {t.source.data}: {source.dataset}, {source.provider} (
      <a href={source.url} target="_blank" rel="noopener noreferrer">{source.repository}</a>). {t.source.licence}:{" "}
      <a href={source.licence_url} target="_blank" rel="noopener noreferrer">{source.licence}</a>. {source.notice}
      {asOf?.latest_arrival_date && <> Latest report in the data: {formatDate(asOf.latest_arrival_date)}.</>}
      {asOf?.last_successful_ingestion_at && <> Last update: {formatDateTime(asOf.last_successful_ingestion_at)}.</>}
    </p>
  );
}

export function CropChips({ commodities, selected, onSelect, label = t.home.crop }: {
  commodities: Pick<Commodity, "id" | "name">[]; selected: number | undefined; onSelect: (id: number) => void;
  label?: string;
}) {
  return (
    <div role="group" aria-label={label} className="chips">
      {commodities.map((c) => (
        <button key={c.id} type="button" className="chip" aria-pressed={c.id === selected}
                onClick={() => onSelect(c.id)}>{c.name}</button>
      ))}
    </div>
  );
}

export function Segmented<T extends string | number>({ label, options, value, onChange }: {
  label: string; options: { value: T; label: string }[]; value: T; onChange: (v: T) => void;
}) {
  return (
    <span className="seg-row">
      <span className="label" id={`seg-${slug(label)}`}>{label}</span>
      <span className="segmented" role="group" aria-labelledby={`seg-${slug(label)}`}>
        {options.map((o) => (
          <button key={String(o.value)} type="button" aria-pressed={o.value === value}
                  onClick={() => onChange(o.value)}>{o.label}</button>
        ))}
      </span>
    </span>
  );
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-");
