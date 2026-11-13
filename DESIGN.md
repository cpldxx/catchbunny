---
version: 1
name: Catchbunny
description: |
  A testing tool with a playful name and a strict job. The interface is a meadow after
  dark: grounds are a green so deep it reads as night, text is moonlit rather than white,
  and the one bright colour on any screen is the moon's pale yellow. The bunny is the logo
  and appears nowhere inside the product. Headlines speak in a casual hand, everything that
  is read is set in a face drawn for legibility, and code is the only thing in monospace.
  On the landing page, numbers the product measured are said loudly: large, counted up, and
  underlined twice as if with a pen. The page leads with what works and names its scope; what
  the product does not do is written down in the docs, one link away.

colors:
  deep: "#08100c"
  page: "#0c1511"
  card: "#111c17"
  raised: "#18261f"
  field: "#0f3a2a"
  text: "#e8efe3"
  secondary: "#a9b7a8"
  muted: "#869585"
  on-field: "#b9cdbf"
  hairline: "rgba(226, 238, 224, 0.09)"
  hairline-strong: "rgba(226, 238, 224, 0.18)"
  hover: "rgba(226, 238, 224, 0.05)"
  accent: "#f3e7a1"
  accent-ink: "#0c1511"
  positive: "#5fd39a"
  negative: "#ff6b68"
  caution: "#e7b550"

typography:
  display-hero: { fontFamily: Recursive Casual, fontSize: 60px, fontWeight: 800, lineHeight: 62px, letterSpacing: -0.025em }
  display-section: { fontFamily: Recursive Casual, fontSize: 40px, fontWeight: 800, lineHeight: 44px, letterSpacing: -0.02em }
  display-band: { fontFamily: Recursive Casual, fontSize: 40px, fontWeight: 800, lineHeight: 46px, letterSpacing: -0.02em }
  number-lead: { fontFamily: Recursive Casual, fontSize: 120px, fontWeight: 800, lineHeight: 104px, letterSpacing: -0.03em }
  number: { fontFamily: Recursive Casual, fontSize: 64px, fontWeight: 800, lineHeight: 64px, letterSpacing: -0.03em }
  page-title: { fontFamily: Recursive Casual, fontSize: 28px, fontWeight: 800, lineHeight: 32px }
  lead: { fontFamily: Atkinson Hyperlegible Next, fontSize: 18px, fontWeight: 400, lineHeight: 28px }
  reading: { fontFamily: Atkinson Hyperlegible Next, fontSize: 16px, fontWeight: 400, lineHeight: 26px }
  body: { fontFamily: Atkinson Hyperlegible Next, fontSize: 14px, fontWeight: 400, lineHeight: 20px }
  strong: { fontFamily: Atkinson Hyperlegible Next, fontSize: 14px, fontWeight: 600, lineHeight: 20px }
  secondary: { fontFamily: Atkinson Hyperlegible Next, fontSize: 13px, fontWeight: 400, lineHeight: 20px }
  code: { fontFamily: Recursive Mono, fontSize: 13px, fontWeight: 400, lineHeight: 20px }
  meta: { fontFamily: Recursive Mono, fontSize: 12px, fontWeight: 400, lineHeight: 16px }

rounded: { control: 8px, small: 6px, panel: 12px, window: 16px }
spacing: { unit: 4px, row: 36px, section: 96px, stage: 112px }
---

# Catchbunny design system

`web/src/theme.ts` holds these values for the code; this file is the account of them.
`PRODUCT.md` holds what the product is and who it is for.

## Overview

Three surfaces, and each is designed for what its visitor came to do.

| Surface | Route | The visitor | So the design |
|---|---|---|---|
| Landing page | `/` | Decides whether to try it | Commits fully: the casual display face, a real run to play with, one region in the meadow's own green |
| Console | `/app` | Finds and reads bugs | Is a working tool first. The brand lends type, palette, density and one signature, and never the layout |
| The ways in | `/app/sign-in`, `/join`, `/setup`, `/authorize` | Gets into a team server | One card, one question, one primary button. Only a team server has them |
| Docs | `/docs` | Looks something up | Is a document first. The frame carries the brand; the column stays calm |

The character is **playful name, exact tool**: friendly in what it says, strict in what it claims.
The page never shows a number the product did not write to a file.

The site offers a console only where there is one. It asks the server what it is (`install.ts`,
`/v1/meta`): served by `catchbunny start` it says "Open the console", on a team server "Sign in",
and on a static copy of the site, which has no server behind it, it offers the quick start instead.

## Colors

Dark was chosen by the owner. The ground is green rather than grey or navy because that is
where a rabbit is, and because a tinted dark is the one thing a grey-plus-accent tool screen
does not have.

- **Grounds** step by lightness: `deep` (the rail, code wells), `page`, `card`, `raised` (inputs, the selected row). Depth comes from these steps, not from shadows.
- **Text** has three roles: `text`, `secondary`, `muted`. `muted` is at least 5:1 on `raised`, so every role passes 4.5:1 on every ground.
- **`accent`, the moon,** has five jobs and no others: the primary action, the logo, what is live, what was caught, and the pen strokes and lead numbers on the landing page. The focus ring and the text selection use it too, because those are the reader's own actions.
- **`field`** is a region, never a card: one band of the landing page is the meadow's colour edge to edge. Text on it is `text` and `on-field`; `muted` does not pass there and is not used.
- **`positive`, `negative`, `caution`** carry verdicts only, as a small light beside a word. Severity is drawn as bars, so it reads without colour.

## Typography

Three faces, each with one job.

- **Recursive Casual, weight 800,** for the name, the headlines, and loud numbers. Inside a sentence, a phrase set in it (weight 700) is the emphasis. It is the playful half of the character.
- **Atkinson Hyperlegible Next** for everything that is read: interface and documentation. It was drawn so that similar letters cannot be mistaken for one another, which is the right face for a tool that checks interfaces. An earlier pass set body text in Recursive's own sans; its letters are close enough to its monospace that code and prose stopped looking different, and that difference is information.
- **Recursive Mono** for code, addresses, and measurements that line up in columns. Nothing is set in monospace to look technical.

Weights are 400 and 600. Sentence case everywhere. Numbers that are compared use tabular
figures. Headings are balanced (`text-wrap: balance`). A duration never breaks from its unit.

## Layout

- A 4px grid. Rows are 36, lane rows 32, panels are padded 16 to 24.
- Landing: a 1200px frame; each stage of a run is a 4/8 split of a title and a working figure, alternating sides; sections are 96 to 112px apart.
- Console: a 256px rail and fluid content. Compact readings above one wide chart; a narrow list beside a wide detail.
- Docs: a 208px rail, a column of at most 72 characters, and the page's sections on the right when there is room.

## Elevation & Depth

Flat. Surfaces step in lightness and are separated by hairlines. The only shadow is under a
popover or a dialog, where something really is above something else. Nothing glows, and
there is no gradient except the hero's ground easing from `deep` to `page`.

## Shapes

Controls 8px, small controls 6px, panels 12px, windows 16px. The bunny is three ellipses:
two ears, one tilted, and a round head. It has no face, so it stays a mark at 16px.

## Emphasis

The landing page raises its voice in three ways (`landing/emphasis.tsx`), and only for
things the product measured or the few words a section turns on.

- **A loud number**: the display face at 64px, or 120px in the moon's colour for the one number a section leads with. It counts up the first time it is seen.
- **Two pen strokes** under a number or a short phrase, drawn left to right when it comes into view. One per section at most.
- **A key phrase** inside a sentence, set in the display face.

A number is always above or beside the evidence it summarises: the benchmark's score sits on
the table of bugs it counts. An earlier pass kept the lower sections to plain tables and
lists; the owner found that they read as notes rather than as a page, and asked for this.

## What the page leads with

The landing page is the commercial face. It leads with what was measured to work, and a
headline names its scope so that it stays true: "nine planted bugs" are the nine that raise an
error, and the sentence under it says so. An earlier pass put the two bugs the no-model run
cannot see into the headline and the table, and listed seven limits on the page; the owner
asked for the page to read as a product, with the no-model result in front. So:

- The benchmark section is the no-model benchmark, complete: nine of nine.
- What a model adds is its own section, with its own measured numbers (`models.json`).
- What the product does not do is the docs' Limits page, linked from the page. It is never
  deleted, only moved.
- Nothing may be stated that a file does not back. Leaving a miss out of a headline is a
  choice of emphasis; saying "all eleven" would be false.

The hand-off stage says what Catchbunny is to an agent first, an MCP server, and puts MCP
before the names of the agents that speak it: the protocol, then who uses it.

## Components

- **Bunny** (`components/marks.tsx`): the logo, in the masthead, the footer and the tab icon, and once on the landing page, where its ears show above the run. It is never a state inside the product: an earlier pass used ears and bunnies as marks on the timeline, and the owner found that too much.
- **Sighting**: a small diamond in the accent. A detector fired here, and nothing is proven yet.
- **Catch**: a disc in the accent with a tick. A defect that replayed. As a ring, one that did not, or a planted bug that was missed.
- **Count**, **Pen**, **Key**, **Stat** (`landing/emphasis.tsx`): the emphasis above.
- **Door** (`pages/DoorPages.tsx`): a way into a team server. A card on the deepest ground with the name, one heading in the display face, what is being asked, and one primary button.
- **Lines** (`pages/ConnectPage.tsx`): a command or a config entry to copy, with a line above it saying where it goes.
- **Buttons**: one primary per screen, in `accent` with dark ink. Everything else is `raised` with a hairline. Outside the console they are plain elements (`.btn`), so those pages carry no component library.
- **Window** (`.window`): a piece of the product shown on a page, with its own title bar.
- **Route table** (`components/RouteTree.tsx`): the map. Rows indented by address; relations are marks in the last column, never lines.
- **Lanes** (`components/Lanes.tsx`): every tester on one clock, on a canvas.
- **Seg**, **Copy** (`components/controls.tsx`): a choice between views, and a button that copies.
- **Note**, **Stamp**, **SeverityMark**, **Pips**: status as a small light and a word; severity as bars; one pip per replay.
- **Command** (`site/Site.tsx`): one command on one line with a copy button.

## Do's and Don'ts

### Do

- Use the bunny only as the logo. Inside the product, a sighting is a diamond and a catch is a ticked disc.
- Give every list its loading, empty and error states, and every error a way to try again.
- Put a real control inside a clickable row, so a keyboard reaches it and a middle click opens it.
- Keep state a reader may want to share in the address (the chosen finding, the chosen route).
- Show a planted bug that was missed in the same table as the ones that were caught.

### Don't

- Don't put a label above a heading, or number sections that are not a sequence. The six stages of a run are a sequence; nothing else on the site is numbered.
- Don't offer the console where there is none, and don't put misses or limits in a headline: they live in the docs, linked.
- Don't show a large number without the evidence it summarises next to it, and never one the product did not measure.
- Don't use grey. Neutrals are tinted from the ground.
- Don't add a second accent, a gradient, a glow, or a card with a coloured edge.
- Don't invent customers, quotes, prices or counts.
- Don't use the mono face for labels.

## Responsive Behavior

- Below 1080px a stage's title sits above its figure, and the benchmark's rows fold to two lines.
- Below 860px the rail becomes one Menu button, the docs rail becomes a row that scrolls, and nothing scrolls sideways.
- Targets are at least 28px high where they sit in a dense tool row and 36 to 44px elsewhere.
- With reduced motion, nothing animates: figures show their finished state.

## Motion

One curve (`cubic-bezier(0.22, 1, 0.36, 1)`). On the landing page a bunny hides behind the
run, and its ears come up again each time the run catches a bug; loud numbers count up and
pen strokes draw themselves, once, when they are first seen. In the console the only
motion is a change of colour or surface on hover, and the light on a run that is running.

## Iteration Guide

1. Decide which surface the change is on, and read its row in the table at the top.
2. Take colours and faces from `theme.ts`. If a value is missing there, it is missing from the system: add it there first.
3. Run `npm run build` in `web/`, then look at the page at 1440px and 375px.

## Known Gaps

- No light theme. Dark is the owner's choice.
- Buttons and headings are in sentence case. Vercel's guidelines ask for Title Case; the other sources and the existing copy use sentence case, and the copy was kept consistent.
- The console's long tables are not virtualised. Runs here have tens of rows, not thousands.
- The hero's second button changes from "Connect your agent" to "Open the console" a moment after load when a server answers; the two are close in width, so the row does not jump.
- The recording in a finding has no declared size until it loads, so the panel shifts once.
- Fonts are not preloaded; they are self-hosted with `font-display: swap`.

## Sources

The system was built by reading six public guides and applying what each asks for.

| Source | What was taken from it |
|---|---|
| [anthropics/skills](https://github.com/anthropics/skills) `frontend-design` | Ground the design in the subject; plan tokens, type and layout before code; spend boldness in one place |
| [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | Its profile for a developer tool: dark, minimal, a product demo then features, with documentation and keyboard shortcuts as requirements |
| [VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md) | The shape of this file |
| [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | One mode per surface (persuade, operate, read); the craft floor; what to refuse (labels above headings, metric heroes, monospace as costume, untouched browser surfaces) |
| [nexu-io/open-design](https://github.com/nexu-io/open-design) `craft/` | The anti-slop list and the five states every surface must have. The app itself was not run |
| [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | The Web Interface Guidelines as an audit, and the bundle rules from its React guide |

The console's first design pass used [unslop](https://github.com/heydamir/unslop) by Damir Sokolovsky (CC BY 4.0), and its 4px grid and quiet chrome are still here.
