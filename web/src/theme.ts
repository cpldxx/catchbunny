// The visual system's values, in one place. DESIGN.md at the project root is the
// account of it; this file is what the code reads.
//
//   Product    Catchbunny sends a swarm of browser testers at a running web app and
//              reports only the defects it could replay in a fresh browser.
//   Character  playful name, exact tool. Friendly in what it says, strict in what it claims.
//   World      a meadow after dark. The ground is a green so deep it reads as night,
//              text is moonlit, and the one bright thing on any screen is the moon's own
//              pale yellow.
//   Signature  the bunny, which is the logo and nothing else. Inside the product a diamond
//              marks a sighting (a detector fired) and a ticked disc a catch (a defect that
//              replayed in a fresh browser): candidate against proven.
//
// Neutrals are tinted from the ground, never grey. Red, green and amber carry verdicts
// only. Lengths sit on a 4px grid.

export const palette = {
  deep: "#08100c", // the rail, code wells: one step below the page
  page: "#0c1511", // page background
  card: "#111c17", // cards and panels
  raised: "#18261f", // inputs, selected rows
  field: "#0f3a2a", // a region that commits to the meadow: a band of a page, never a card
  text: "#e8efe3", // primary text: moonlit, not white
  secondary: "#a9b7a8", // body that is not the point
  muted: "#869585", // meta, captions, icons. 5:1 on the darkest raised surface
  "on-field": "#b9cdbf", // secondary text on a field, tinted from it
  hairline: "rgba(226, 238, 224, 0.09)", // borders: the text colour, thinned
  "hairline-strong": "rgba(226, 238, 224, 0.18)",
  hover: "rgba(226, 238, 224, 0.05)", // a row under the pointer
  accent: "#f3e7a1", // the moon: the primary action, the bunny, what is live, what was caught
  "accent-ink": "#0c1511", // text on the accent
  positive: "#5fd39a",
  negative: "#ff6b68",
  caution: "#e7b550",
} as const;

// Three faces, each with one job (the @font-face rules are in styles.css):
//   ui       Atkinson Hyperlegible Next. Everything that is read. Drawn so that similar
//            letters cannot be mistaken, which suits a tool that checks interfaces.
//   mono     Recursive Mono. Code, addresses, and measurements that line up in columns.
//   display  Recursive Casual. The name and the headlines: the playful half of the character.
export const fonts = {
  ui: '"Atkinson Hyperlegible Next", system-ui, -apple-system, "Segoe UI", sans-serif',
  mono: '"Recursive Mono", ui-monospace, "SF Mono", Menlo, monospace',
  display: '"Recursive Casual", "Atkinson Hyperlegible Next", system-ui, sans-serif',
};

export const ease = "cubic-bezier(0.22, 1, 0.36, 1)"; // the one curve

export function installTokens(): void {
  const root = document.documentElement.style;
  for (const [name, value] of Object.entries(palette)) root.setProperty(`--${name}`, value);
  for (const [name, value] of Object.entries(fonts)) root.setProperty(`--font-${name}`, value);
  root.setProperty("--ease", ease);
}
