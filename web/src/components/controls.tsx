// Two small controls every surface uses: a choice between a few views, and a button
// that copies text. Plain elements, styled in styles.css.

import { useState } from "react";

/** A choice between a few views of the same thing. */
export function Seg<T extends string>({ value, onChange, options, label }: { value: T; onChange: (value: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className={o.value === value ? "on" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function Copy({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button className="copy" type="button" onClick={async () => {
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      } catch { /* clipboard unavailable: the text is on screen to select */ }
    }}>
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </button>
  );
}
