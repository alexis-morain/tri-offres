---
name: tri-offres
description: A quiet white page, like a well-kept notebook, where four pale tags say what each answer is.
colors:
  ground: "#ffffff"
  surface: "#ffffff"
  wash: "#f7f7f5"
  line: "#e9e9e7"
  ink: "#37352f"
  ink-muted: "#6b6a65"
  field-border: "#8e8c86"
  disabled: "#e9e9e7"
  action: "#191918"
  action-hover: "#3b3a36"
  action-tint: "#efefed"
  on-action: "#ffffff"
  link: "#1d63c9"
  focus: "#1d63c9"
  error: "#c4332b"
  error-tint: "#fbeceb"
  pink: "#f8e4ec"
  pink-ink: "#6e2a48"
  pink-rule: "#c0477f"
  blue: "#e1edf6"
  blue-ink: "#1d4466"
  blue-rule: "#3a78ad"
  yellow: "#fbf0d4"
  yellow-ink: "#5c4210"
  yellow-rule: "#a87316"
  green: "#e3f0e4"
  green-ink: "#245133"
  green-rule: "#3f8458"
typography:
  display:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  headline:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.3
  lede:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1.45
  body:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  body-small:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  caption:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
  tag:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.3
  figures:
    fontFamily: "{typography.display.fontFamily}"
    fontFeature: "tnum"
  code-entry:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1.5rem"
    fontWeight: 600
    letterSpacing: "0.15em"
    fontFeature: "tnum"
rounded:
  small: "4px"
  control: "6px"
  panel: "10px"
  pill: "999px"
spacing:
  space-1: "0.25rem"
  space-2: "0.5rem"
  space-3: "0.75rem"
  space-4: "1rem"
  space-5: "1.5rem"
  space-6: "2rem"
  space-7: "3rem"
  space-8: "4.5rem"
components:
  button-primary:
    backgroundColor: "{colors.action}"
    textColor: "{colors.on-action}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "0 1.25rem"
    height: "2.75rem"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "2.75rem"
  button-secondary-hover:
    backgroundColor: "{colors.wash}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.error}"
    rounded: "{rounded.control}"
    height: "2.75rem"
  button-danger-hover:
    backgroundColor: "{colors.error-tint}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
    height: "2.75rem"
  tag-pink:
    backgroundColor: "{colors.pink}"
    textColor: "{colors.pink-ink}"
    typography: "{typography.tag}"
    rounded: "{rounded.small}"
    padding: "0.125rem 0.5rem"
  tag-blue:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.blue-ink}"
  tag-yellow:
    backgroundColor: "{colors.yellow}"
    textColor: "{colors.yellow-ink}"
  tag-green:
    backgroundColor: "{colors.green}"
    textColor: "{colors.green-ink}"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "1rem 1.25rem 1.25rem"
  callout-ai:
    backgroundColor: "{colors.green}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0.5rem 0.75rem"
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    height: "2.5rem"
  option-selected:
    backgroundColor: "{colors.action-tint}"
---

# Design System: tri-offres

## Overview

**North Star: "The Quiet Page"**

tri-offres looks like a well-kept notebook page: white, wide margins, warm grey ink, nothing that asks for attention except the one decision on screen. The references are Notion (page, properties, coloured tags, callouts), Twenty (dense lists that stay calm) and OpenSEO (near-black actions, a plain sans set with care). The interface is a working screen: people come once to answer a few questions, then a few minutes a day to sort offers. Calm and legible beat clever.

What carries the product's meaning is kept from the previous system and only redressed: four kinds of information, four colours, now shown as pale **tags** the way Notion labels a property. Pink is what you exclude, blue what you prefer, yellow what you say in your own words, green what Claude wrote. Everything else is grey.

**Key Characteristics:**
- White ground, warm grey ink (`#37352f`), one hairline colour (`#e9e9e7`).
- Near-black buttons; blue only for links and the focus ring.
- Four tag colours, one per kind of information, never decorative.
- The platform's own sans (San Francisco, Segoe UI), no webfont at all.
- Panels with a 1px hairline and 10px corners; no shadow except on what floats.
- Progress named by block with the time left in minutes, never step numbers or percentages.

## Colors

Neutral, warm, low-contrast surfaces; contrast is spent on text and on the single primary action.

### Neutrals
- **Page White** (`ground`, `surface`): page, fields, panels. Also the `theme-color`. Both tokens stay so a future tint of the ground costs one line.
- **Wash** (`wash`): the quiet second surface. Notices, the folded file band on phones, the source excerpts under Claude's sentences, hovered secondary buttons, the date block of the end screen.
- **Hairline** (`line`): every 1px divider and panel border, the masthead and footer rules, resting option rows.
- **Ink** (`ink`): body text, headings, the switch knob when off, gauge notches that are on. A warm grey-black, never pure black.
- **Muted ink** (`ink-muted`): the "pourquoi" line under a question, hints, row labels, footer, inactive tabs.
- **Field border** (`field-border`): the 1px border of fields, chips and the switch track, the dashed drop zone; it holds 3:1 on white so a field reads as a field.
- **Spent** (`disabled`): disabled button fill, with muted ink text.

### Action
- **Near Black** (`action`): the primary button's fill, the checked radio and checkbox (`accent-color`), the switch when on, the current tab's underline. Hover lightens to `action-hover`. `action-tint` is the selected option row and the armed secondary sort button. `on-action` is white text on black.
- **Link Blue** (`link`, `focus`): links in running text and the 2px focus ring. Nothing else is blue.

### Error
- **Red** (`error`, `error-tint`): field errors, the error summary, the danger button and the "Supprimer" checkbox of a CV line. One red only: errors always add the alert icon and bold weight, so a destructive button and an error never get confused.

### Tags (the four kinds)
Each kind has a pale fill, a dark ink for text on that fill, and a mid "rule" for small marks on white (the progress dot, the chip's checked border, the brand mark).
- **Pink** (`pink`, `pink-ink`, `pink-rule`): « Ce que vous excluez », the filters. The live offer counter sits on its panel.
- **Blue** (`blue`, `blue-ink`, `blue-rule`): « Ce que vous préférez ».
- **Yellow** (`yellow`, `yellow-ink`, `yellow-rule`): « En vos mots », free text and the CV.
- **Green** (`green`, `green-ink`, `green-rule`): « Ce que j’ai compris » and every text Claude wrote. The reason under an offer sits in a green callout; the « rédigée par IA » label is green ink.

### Named Rules
**The One Tag Per Kind Rule.** Pink, blue, yellow and green each mean one kind of information and keep it on every surface. A new surface reuses a colour only for the same kind, never as decoration.

**The Grey By Default Rule.** A panel is white with a hairline; only its title tag carries the colour. Fills are reserved for tags, checked chips and Claude's callouts.

**The Tested Pair Rule.** Every text and boundary pairing is held to RGAA AA by `ui/tests.py`: 4.5:1 for text, 3:1 for boundaries. A new pairing is added to the test before it ships.

## Typography

**Family:** the platform's sans, `ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif`. Nothing is downloaded: the page looks native on each device, like Notion.

**Character:** plain and exact. Hierarchy comes from size and weight steps, not from a second family. Figures that the system gives back (counters, dates, scores, page numbers) use tabular figures through the `.typed` class, in the same family.

### Hierarchy
- **Display** (700, 2rem, 1.2, -0.015em): the one `h1` per screen, the question itself. Balanced wrapping; « voulez-vous » never breaks at the hyphen.
- **Headline** (600, 1.25rem): `h2` section titles.
- **Title** (600, 1rem): panel titles outside tags, `h3`, offer titles.
- **Lede** (400, 1.25rem, 1.45): the opening promise on the home page and the arrival sentence; capped at 34rem.
- **Body** (400, 1rem, 1.5): running text, field values, option rows; prose capped at 38rem.
- **Body small** (400, 0.875rem): hints, "pourquoi" lines in dense views, panel rows, footer, tabs.
- **Label** (600, 1rem): field labels and legends, buttons.
- **Caption** (400, 0.8125rem): row labels, meta lines, the AI label. Never smaller.
- **Tag** (600, 0.8125rem): the coloured tag that names a panel.
- **Code entry** (600, 1.5rem, 0.15em tracking, tabular): the six-digit sign-in code.

### Named Rules
**The Native Type Rule.** No `@font-face`, no CDN, no `@import`. Weights used: 400, 600, and 700 for the display title and error messages only.

**The Figures Rule.** Numbers the system reports are tabular (`.typed`), never in a monospace face.

## Layout

A single centred column, 72rem at most with 1rem side padding, shared by masthead, page and footer. Pages open with 2rem above and 4.5rem below. Spacing follows an eight-step scale (0.25 to 4.5rem); 2rem between fields, 3rem between the question and its actions. White space does the separating; rules are the exception.

The question screen is the canonical layout. Below 60rem it is one column, the file comes first as a folded band on a wash ground (« Votre fichier », the count, the counter). From 60rem it is two tracks: the question at up to 37rem on the left, the file at 19rem on the right, sticky 1.5rem from the top. The primary button sits bottom left, the back link beside it.

The block progress sits above every questionnaire screen: three block names in small text, each with a dot in its tag rule colour, the current one in ink with a 2px ink underline; the time left in minutes on the right. Below 30rem the three blocks become three equal cells.

The summary spans both tracks: from 48rem the panels sit in two columns, pink spanning two rows, green across the full width with its text capped at 44rem. The home page pairs the intro (36rem) with a sample mail panel (25rem) from 60rem. « Mes données » is a two-column property list from 40rem (11rem labels), like Notion's page properties.

**The One Decision Rule.** A screen asks one question with its "pourquoi" line directly under it; the file beside it is the only other thing in view.

## Elevation & Depth

Flat by default. Separation comes from white space first, a 1px hairline second, a wash ground third. One shadow exists, `--shadow-float`, the layered menu shadow of Notion, and only on what floats above the page: the suggestions list under a field and the offer being dragged on the sort page.

**The Float Only Rule.** A shadow means "this is above the page and will go away". Panels, cards and buttons never get one.

## Shapes

Three corners, each with one job: 4px for small marks (tags, the chip's check box), 6px for controls (buttons, fields, chips, option rows), 10px for panels (cards, notices, the error summary, the sort sheet, suggestions). The switch is a pill with a round knob; radios are native circles. Borders are 1px everywhere; the only 2px lines are the current tab's underline, the focus ring and a hovered link's underline. The tiniest marks (brand squares, gauge notches) take 2px corners.

The brand mark is three small squares (pink, blue, yellow rule colours, 2px corners) beside the wordmark in 600 weight: the product's tags, not a logo drawing.

Icons are inline SVG on a 20px grid, stroke in currentColor, always decorative (`aria-hidden`).

## Components

### Buttons
- **Primary:** Near Black fill, white 600 label, 6px corners, 2.75rem tall, 1.25rem side padding. One per screen, bottom left of the form. Hover lightens to `action-hover` in 160ms; pressed moves down 1px.
- **Secondary:** white with a 1px field border and ink text; hover fills with Wash.
- **Danger:** white with a 1px red border and red text; hover fills with `error-tint`. Lives only in the danger zone, under a hairline, far from any primary button.
- **Disabled:** Spent fill, muted text. **Busy:** keeps its label and adds a small spinning ring, real work only.
- **Sort page:** three equal columns, « Pas pour moi » left, « Peut-être » centre, the primary « Ça m’intéresse » right, because the swipe to the right says yes.
- **Back link:** muted ink with the back icon, ink on hover, underlined.

### Tags
The colour of the product. A tag is a 4px-cornered pill of pale fill and dark ink, 600 weight, 0.8125rem: it names a panel (« Ce que vous excluez »), never a value.

### Panels (cards)
- White, 1px hairline, 10px corners, padding 1rem 1.25rem 1.25rem.
- Head: the tag on the left, meta or « Modifier » on the right; no rule under it.
- Rows: label (caption, muted) over value. Missing answers read « Non renseigné » in italic muted ink.
- The pink panel carries the offer counter under a hairline, in tabular figures.
- **Callout:** a panel without border on a tag fill (`card--strip`), for Claude's reason under an offer.
- **Sort sheet:** a white panel for one offer: title, employer, meta line, gauge, the reason in a green callout, summary, source. The next offer waits behind, offset by 0.5rem. While dragged, the top sheet gets the float shadow.

### Fields
White, 1px field border, 6px corners, 2.75rem tall; textareas from 7.5rem. Label 600 above, hint in small muted text under it. Focus: border and 2px ring in Link Blue. Error: border red, message above the control in bold red with the alert icon; a form back with errors opens on an error summary (red border, `error-tint` ground) that takes focus.

### Chips
Checkboxes drawn as chips for capped choices: white, 1px field border, 6px corners, 2.5rem tall, a 1rem check box always in the layout. Checked: the block's tag fill and rule border. At the cap the unchecked ones turn dashed and muted; a "1 sur 3" count sits under the group.

### Options
One row per choice, native radio or checkbox in Near Black. Resting border Hairline, hover Field border, checked Field border on `action-tint`: the control itself carries the state.

### Switch
« Plutôt une préférence »: a 2.75 by 1.5rem pill. Off is an ink knob on white with a field border, never a greyed-out control; on is a white knob on Near Black. Announced as `role="switch"`.

### Navigation
- **Masthead:** white, wordmark left, one action right, a hairline under it.
- **Account tabs** (« Mes offres », « Ma recherche », « Mes données »): small muted text, current one in ink, 600, with a 2px ink underline, like Notion's view tabs.

### The File (signature)
« Votre fichier » with its count, then the panels in block order, 1.5rem apart, each named by its tag. Open on wide screens, a folded band on phones that still shows the counter. A panel filed on this request drops in from 0.75rem above over 320ms.

### The Answer Lines
At the summary and in « Ma recherche » each panel lists one line per answer, label over value, a « Modifier » link on the right. Claude's sentences on the green panel each open on the answers they come from, as a disclosure with a dotted underline; the excerpt sits on Wash.

### The Date
The first-send date on a Wash block with a hairline and 10px corners, 600 weight, tabular, « vers 7 h » under it in muted ink. It rises in once on arrival (240ms, opacity and transform), never again for a date already gone. Class names stay `stamp` for the tests.

### The Gauge
A score is always ten notches, 0.5 by 1rem with 2px corners, ink when on and white with a field border when off, followed by « 7 sur 10 » in tabular 600.

### Motion
Controls ease over 160ms on `cubic-bezier(0.16, 1, 0.3, 1)`. Two moments only: a panel filed (320ms) and the date rising (240ms). Under `prefers-reduced-motion`, everything collapses to 1ms.

### Mails
Same world, inline styles only, no image. On a Wash ground: the brand mark and wordmark above, then one white panel (1px hairline, 10px corners). In the morning mail each offer is its own bordered panel: title 600, employer and place muted, « 8 sur 10 » in tabular 600, Claude's reason in a green callout under « Raison rédigée par IA », source and « Voir l’offre » on one small muted line. The sign-in code sits on a Wash block. The near-black button (6px corners), then « Notes générées par IA »; the unsubscribe line closes the panel under a hairline.

## Do's and Don'ts

### Do:
- **Do** keep the page white and the text warm grey; let space separate things.
- **Do** name each panel with its tag and keep the four colours to their four kinds.
- **Do** use Near Black for the one primary action and blue for links and focus only.
- **Do** use 1px borders, and the three corners for their three jobs.
- **Do** set reported numbers in tabular figures (`.typed`).
- **Do** name progress by block and give the time left in minutes.
- **Do** build every control on a native input and make every page work without JavaScript.
- **Do** add every new colour pairing to the contrast test.
- **Do** write French copy in the vouvoiement with curly apostrophes (’) and French quotes (« »); CSS classes stay English BEM.

### Don't:
- **Don't** load a font, from a CDN or from the project.
- **Don't** fill a whole panel with a tag colour; only tags, checked chips and Claude's callouts get a fill.
- **Don't** put a shadow on anything that does not float.
- **Don't** add a coloured border on one side of a panel.
- **Don't** use purple, gradients, glassmorphism or a second accent colour.
- **Don't** show step numbers or a percentage progress bar.
- **Don't** put a destructive action next to a primary button.
- **Don't** use an em dash anywhere in the interface copy.
