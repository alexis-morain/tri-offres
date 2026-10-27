---
name: tri-offres
description: Job offers sorted like coloured index cards filed in a small, visible drawer.
colors:
  ground: "#f3f5f9"
  surface: "#fcfdfe"
  line: "#d3d9e4"
  ink: "#18202e"
  ink-muted: "#465166"
  field-border: "#6a7489"
  disabled: "#c9cfdb"
  action: "#1f3cc4"
  action-hover: "#172e9a"
  action-tint: "#e4e9fb"
  on-action: "#ffffff"
  stamp: "#b8262b"
  stamp-tint: "#fbe9e9"
  error: "#a3271f"
  pink: "#f6d4de"
  pink-ink: "#6b2c40"
  pink-rule: "#b9476a"
  blue: "#cfe1f4"
  blue-ink: "#1f4468"
  blue-rule: "#3b72b0"
  yellow: "#f7ec9f"
  yellow-ink: "#5a4a06"
  yellow-rule: "#8a7408"
  green: "#d2ead0"
  green-ink: "#2a5326"
  green-rule: "#3f8a3a"
typography:
  display:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.2
  title:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 700
    lineHeight: 1.2
  lede:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1.45
  body:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 400
    lineHeight: 1.55
  body-small:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 700
    lineHeight: 1.55
  caption:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.55
  typed:
    fontFamily: "Courier Prime, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0"
    fontFeature: "tnum"
  stamp:
    fontFamily: "Courier Prime, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "1.0625rem"
    fontWeight: 700
    lineHeight: 1.25
    fontFeature: "tnum"
  code-entry:
    fontFamily: "Luciole, Atkinson Hyperlegible, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    letterSpacing: "0.15em"
    fontFeature: "tnum"
rounded:
  corner: "2px"
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
    rounded: "{rounded.corner}"
    padding: "0 1.5rem"
    height: "3rem"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
    textColor: "{colors.on-action}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.action}"
    rounded: "{rounded.corner}"
    padding: "0 1.5rem"
    height: "3rem"
  button-secondary-hover:
    backgroundColor: "{colors.action-tint}"
    textColor: "{colors.action-hover}"
  button-danger:
    backgroundColor: "transparent"
    textColor: "{colors.stamp}"
    rounded: "{rounded.corner}"
    padding: "0 1.5rem"
    height: "3rem"
  button-danger-hover:
    backgroundColor: "{colors.stamp-tint}"
    textColor: "{colors.stamp}"
  button-disabled:
    backgroundColor: "{colors.disabled}"
    textColor: "{colors.ink-muted}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.corner}"
    padding: "0.625rem 0.75rem"
    height: "3rem"
  input-code:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.code-entry}"
    width: "12rem"
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.5rem 1rem"
    height: "2.75rem"
  chip-selected-pink:
    backgroundColor: "{colors.pink}"
  chip-selected-blue:
    backgroundColor: "{colors.blue}"
  chip-selected-yellow:
    backgroundColor: "{colors.yellow}"
  chip-selected-green:
    backgroundColor: "{colors.green}"
  option:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.5rem 1rem"
    height: "3rem"
  option-selected:
    backgroundColor: "{colors.action-tint}"
  card-pink:
    backgroundColor: "{colors.pink}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.75rem 1rem 1rem"
  card-blue:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.75rem 1rem 1rem"
  card-yellow:
    backgroundColor: "{colors.yellow}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.75rem 1rem 1rem"
  card-green:
    backgroundColor: "{colors.green}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.75rem 1rem 1rem"
  card-sheet:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.corner}"
    padding: "0.75rem 1rem 1rem"
  stamp:
    backgroundColor: "transparent"
    textColor: "{colors.stamp}"
    typography: "{typography.stamp}"
    rounded: "{rounded.corner}"
    padding: "0.5rem 1rem"
---

# Design System: tri-offres

## Overview

**Creative North Star: "The Card File"**

Sorting job offers is filing bristol index cards. Every answer the person gives becomes a card of one colour, filed in a small drawer that stays in view: pink for what they exclude, blue for what they prefer, yellow for what they say in their own words, green for what Claude understood. The drawer fills as the questionnaire goes; at the check screen the cards spread open into editable lines; at the end a date stamp prints the day of the first mail, once.

The ground is a cold, faintly blue white, never cream. Cards are flat colour with no texture and no shadow, 2px corners, a printed rule under their title and a tab that sticks out of the top edge like a drawer divider. One saturated school-ink blue carries every action. A rubber-stamp red is held back for the first-send date and for destructive actions. Text is set in Luciole, a face drawn for low vision; data the machine types back (counters, dates, seconds, scores) is set small in Courier Prime with tabular figures.

The screens are working screens, not posters: a fixed rem scale at a ratio of about 1.2, one decision in front of the person, standard web controls built on native inputs, and every page usable without JavaScript. The visual rejection is confirmed in the direction contract: no grey SaaS form with a percentage progress bar, no numbered "step 1-2-3" wizard.

**Key Characteristics:**
- Four card colours, one per kind of information, held across every surface.
- Flat bristol cards with a top tab, a 2px printed rule and 2px corners; no shadows anywhere.
- One action colour (school ink) for buttons, links, focus, selection and the caret.
- Stamp red and error red are separate tokens with separate jobs.
- Luciole for all text; Courier Prime only for small typed data.
- Progress named by block with the time left in minutes, never step numbers or percentages.
- Motion limited to two moments (a card filed, the stamp pressed) and short control transitions.

## Colors

A cold paper ground, one saturated ink for action, one stamp red, and four pale bristol fills, each with its own darker ink and rule.

### Primary
- **School Ink Blue** (`action`): every button fill, every link, the focus ring (`focus` is the same value), the selected radio row's border, the switch when on, the caret and the account menu's current underline. Hover deepens it to **Night Ink** (`action-hover`). **Ink Wash** (`action-tint`) is its pale tint: selected option rows, the secondary button's hover, suggestion rows, text selection, the CV drop zone while a file is over it. **Paper White** (`on-action`) is text on ink.

### Secondary
- **Stamp Red** (`stamp`): the date stamp on the end screen and destructive actions (the danger button, the "Supprimer" checkbox on a CV line via `accent-color`). **Stamp Wash** (`stamp-tint`) is the danger button's hover.

### Tertiary (the bristol cards)
Each card has three tokens: a pale fill, an ink for secondary text on the card (row labels, meta, empty states), and a rule for its header line, its tab in the progress bar and the dashed counter divider.
- **Bristol Pink** (`pink`, `pink-ink`, `pink-rule`): « Ce que vous excluez », the filters. Carries the live offer counter.
- **Bristol Blue** (`blue`, `blue-ink`, `blue-rule`): « Ce que vous préférez », the preferences.
- **Bristol Yellow** (`yellow`, `yellow-ink`, `yellow-rule`): « En vos mots », free text and the CV. The CV list on the CV screen opens under a yellow rule.
- **Bristol Green** (`green`, `green-ink`, `green-rule`): « Ce que j’ai compris », text Claude wrote. Also the reason strips in the sample mail on the home page, and the colour of the « Synthèse rédigée par IA » label.

### Neutral
- **Cold Paper** (`ground`): page background, also the `theme-color`.
- **Sheet White** (`surface`): fields, chips, option rows, notices, the white "sheet" card, source excerpts inside the green card.
- **Pencil Line** (`line`): hairlines (1px) between rows, masthead and footer borders, resting option-row borders.
- **Ink Black** (`ink`): body text, headings, the switch's knob when off, gauge notches that are on.
- **Pencil Grey** (`ink-muted`): the "pourquoi" line under a question, hints, footer, inactive block names.
- **Field Grey** (`field-border`): 2px borders of fields, chips, the switch track, the file drop zone (dashed); tested at 3:1.
- **Spent Grey** (`disabled`): disabled button fill.
- **Error Red** (`error`): field errors, the error summary's border and links, the summary failure line. Always with the alert icon and bold text.

### Named Rules
**The One Card Per Kind Rule.** Pink, blue, yellow and green each mean exactly one kind of information (exclusions, preferences, own words, Claude’s text) and keep that meaning on every surface: drawer, progress tabs, chips selected in that block, the summary, « Ma recherche ». A new surface reuses a colour only for the same kind of information, never as decoration.

**The Card Ink Rule.** Text on a card takes the card’s own ink (`*-ink`), never the page’s Pencil Grey. Primary text on a card stays Ink Black; links on a card stay School Ink.

**The Two Reds Rule.** Stamp red marks the first-send date and destructive actions; error red marks errors. They are separate tokens and are never swapped. Because the two hues sit close, an error is always also carried by the alert icon and bold weight, and the stamp by its double rule and typed face.

**The Tested Pair Rule.** Every text and boundary pairing is held to RGAA AA by `ui/tests.py`: 4.5:1 for text (ink, muted ink, action, stamp and error on ground and surface; ink, card ink and action on each card), 3:1 for boundaries (field border, focus, each card rule on its card and on the ground). A new pairing is added to the test before it ships.

## Typography

**Display Font:** Luciole (with Atkinson Hyperlegible, system-ui, sans-serif), self-hosted in regular, italic and bold.
**Body Font:** Luciole, the same family.
**Label/Mono Font:** Courier Prime (with ui-monospace, SF Mono, Menlo), self-hosted in regular and bold.

**Character:** A legibility face built for low vision, used plainly at a working size, beside a typewriter face that reads as data typed onto a card. No webfont CDN, no `@import`.

### Hierarchy
- **Display** (700, 1.875rem, 1.2, -0.01em): the one `h1` per screen, the question itself. Balanced wrapping; verb-pronoun inversions (« voulez-vous ») never break at the hyphen.
- **Headline** (700, 1.25rem, 1.2): `h2` section titles, block titles on « Ma recherche ».
- **Title** (700, 1.0625rem, 1.2): card titles and `h3`.
- **Lede** (400, 1.25rem, 1.45): the opening promise on the home page and the arrival sentence on the end screen; capped at 34rem.
- **Body** (400, 1.0625rem, 1.55): running text, field values, option rows; prose blocks capped at 38rem.
- **Body small** (400, 0.9375rem): hints, "pourquoi" lines in dense views, card rows, footer, progress names; capped at 34rem when it is a hint.
- **Label** (700, 1.0625rem): field labels and legends, buttons, back links, « Modifier » links.
- **Caption** (400, 0.8125rem): row labels (`dt`) on cards, card meta, the AI label, drawer note.
- **Typed** (Courier Prime 400, 0.9375rem, 1.4, tabular figures): the offer counter on the pink card and in the folded drawer, countdown seconds, the gauge value.
- **Stamp** (Courier Prime 700, 1.0625rem, 1.25, tabular): the date stamp; its second line drops to 400 at 0.9375rem.
- **Code entry** (Luciole 700, 1.5rem, 0.15em tracking, tabular): the six-digit sign-in code field.

### Named Rules
**The One Family Rule.** Everything a person reads is Luciole. The scale is fixed rem steps (0.8125, 0.9375, 1.0625, 1.25, 1.5, 1.875rem); there is no fluid display size.

**The Typed Data Rule.** Courier Prime appears only for data the system types back to the person (counts, dates, seconds, scores), small and with tabular figures. It never sets a heading, a label or a sentence of prose.

## Layout

A single centred column, 72rem wide at most with 1rem side padding, shared by masthead, page and footer. Pages open with 2rem above and 4.5rem below. Spacing follows an eight-step scale (0.25 to 4.5rem); 2rem separates fields, 3rem separates the question from its actions.

The question screen is the canonical layout. Below 60rem it is one column, and the drawer comes first in the source so it sits above the question as a folded band (« Votre fichier », the card count, the typed counter). From 60rem it becomes two tracks, the question at up to 37rem on the left and the drawer at 19rem on the right, pushed apart, the drawer sticky 1.5rem from the top. The primary button sits bottom left with the back link beside it.

The block progress sits above every questionnaire screen: three named blocks with a small coloured tab each and the time left in minutes at the right. Below 30rem the three blocks become three equal cells, each tab above its name.

The summary spans both tracks: from 48rem the cards sit in two columns, pink spanning two rows, green across the full width with its text capped at 44rem. The home page pairs the intro (36rem) with a sample mail sheet (25rem) from 60rem. The end screen shows the whole drawer beside the stamp on wide screens and drops it on phones. « Mes données » is a two-column definition list from 40rem (11rem labels).

**The One Decision Rule.** A screen asks one question with its "pourquoi" line directly under it; the file beside it is the only other thing in view.

## Elevation & Depth

Flat. There is no `box-shadow` anywhere in the system. Depth reads from the filing metaphor instead: a card’s tab rising 0.5rem above its top edge, the card’s square top-left corner where the tab joins, the 2px printed rule under each card title, and the colour of the fill against the cold ground. Separation between rows is a 1px hairline in Pencil Line or in the card’s rule colour. The stamp gets its weight from a 2px border plus a 1px outline offset by 3px, a printed double frame, not a shadow.

**The Flat Bristol Rule.** Cards are flat colour, without texture, gradient or shadow. If something needs to stand apart, give it a rule, a tab or a colour, never a lift.

## Shapes

One corner: 2px, on every card, field, chip, option row, button, notice, swatch and the stamp. Cards square their top-left corner (`0 2px 2px 2px`) where the tab, 3.5rem wide and 0.5rem tall with 2px top corners, sits. Tabs step right by 4rem per block (pink 0, blue 4rem, yellow 8rem, green 12rem) so a stack of cards reads as a drawer. The switch knob is square (1px corners). The only circles are native radio buttons and the busy spinner inside a button.

Borders are 2px for anything interactive (fields, chips, buttons, option rows) and for printed rules, 1px for hairlines and container outlines. The brand mark is three card tabs in filing order (pink, blue, yellow) at decreasing heights beside the wordmark.

Icons are authored inline SVG on a 20px grid, 2px stroke, square caps and mitred joins, the same drawing hand as the corners, always decorative (`aria-hidden`): check, alert, back, next, close, file, mail, edit.

## Components

### Buttons
Solid and plain, one weight.
- **Shape:** 2px corners, 2px border, 3rem minimum height, 1.5rem side padding, Luciole bold.
- **Primary:** School Ink fill, white text. One per screen, bottom left of the form.
- **Hover / Active / Focus:** fill and border deepen to Night Ink in 160ms; pressed moves down 1px; focus is a 3px School Ink outline offset 2px.
- **Sort page exception:** three equal columns, 1rem apart, « Pas pour moi » left, « Peut-être » centre, the primary « Ça m’intéresse » right, because the swipe to the right says yes.
- **Secondary:** transparent with an ink border and ink text; hover fills with Ink Wash.
- **Danger:** transparent with a stamp-red border and text; hover fills with Stamp Wash. Lives only in a danger zone, under a hairline 4.5rem below the content, away from any primary button.
- **Disabled:** Spent Grey fill, Pencil Grey text. **Busy:** keeps its label and adds a small spinning ring, real work only.
- **Back link:** a bold underlined link with the back icon, never a button. Links underline at 1px and thicken to 2px on hover.

### Chips
Checkboxes drawn as chips, for choices with a cap.
- **Style:** Sheet White, 2px Field Grey border, 2.75rem tall, a 1rem check box always in the layout so a chip never changes width.
- **State:** hover borders in ink; checked fills with the block’s card colour and borders with its rule (tone set per field); at the cap, the unchecked ones turn dashed and muted. A "1 sur 3" count sits under the group.
- **Groups:** a long list (the jobs of block 2) splits into groups, each a `role="group"` named by its small bold Ink Muted title, 1.5rem between groups; one cap and one count for the whole field.

### Options
One row per choice for a single or short multiple choice, with the native radio or checkbox in School Ink. Resting border Pencil Line, hover Field Grey, checked School Ink border on Ink Wash.

### Switch
« Plutôt une préférence »: a 2.75 by 1.5rem square track with a square knob. Off is an ink knob on white, never a greyed-out control; on is a white knob on School Ink. Announced as `role="switch"`.

### Cards / Containers
- **Corner Style:** 2px, top-left square under the tab.
- **Background:** one of the four bristol fills, or Sheet White for the "sheet" (the morning mail sample, with a 1px line border and a line-coloured tab).
- **Shadow Strategy:** none (see Elevation & Depth).
- **Border:** none on bristol cards; a 2px rule in the card’s rule colour under the title.
- **Internal Padding:** 0.75rem top, 1rem sides and bottom. Rows are label (caption, card ink) over value. A "strip" is a tabless card used inside a sheet.
- **Sort card:** a Sheet White "sheet" for one offer on the sort page: title, employer in body weight right under it, meta line, gauge, the reason in a green strip, summary, source. The next offer waits behind it, offset by 0.5rem, no shadow, out of reach.
- **Notice:** Sheet White with a 1px line border, for guidance that is not an error.

### Inputs / Fields
- **Style:** Sheet White, 2px Field Grey border, 2px corners, 3rem tall; textareas from 7.5rem, vertical resize. Label bold above, hint in small muted text under it, then the control.
- **Focus:** border turns School Ink plus the 3px focus outline.
- **Error / Disabled:** the field’s border turns Error Red, the message sits above the control in bold error red with the alert icon. A form returned with errors opens with an error summary (2px error border) that takes focus and links to each field.
- **Consequence line:** under a filter, a small sentence says what it costs, kept current as the person types.
- **City suggestions:** a listbox under the field, Sheet White, 2px Field Grey border, the active row in Ink Wash.
- **File field:** the native control without JavaScript; with it, a secondary button labelled in French and, on computers, a dashed drop zone that turns ink on drag-over.

### Navigation
- **Block progress:** three block names in small text, each with a 0.75 by 0.5rem tab in its rule colour; the current one in ink and bold with a 2px underline in its rule colour; done ones with a check icon. Time left in minutes at the right.
- **Account menu** (« Ma recherche », « Mes données »): small muted links, hover to ink with a line underline, current page bold with a 2px School Ink underline.
- **Masthead:** the tab mark and wordmark left, one action right; a 1px line underneath.

### The Drawer (signature)
« Votre fichier » with its card count, then the cards in block order, 1.5rem apart. Rendered open so it works without JavaScript; on phones the script folds it into a band that still shows the typed counter. A card filed on this request drops in from 0.75rem above over 320ms (starting visible, never fading in). The pink card carries the counter below a dashed rule in its rule colour.

### The Answer Lines
At the summary and in « Ma recherche » the cards spread open: one line per answer, label over value, a bold « Modifier » link on the right with a hidden suffix naming the answer. Missing answers read « Non renseigné » in italic card ink. Claude’s sentences on the green card open on the answers they come from, each sentence a disclosure underlined in a dotted card rule.

### The Date Stamp (signature)
The first-send date in uppercase typed bold, stamp red, 2px border plus a 1px outline 3px out, tilted -2.5 degrees, with « vers 7 h » under it. Pressed once on arrival: 300ms from 118% scale and zero opacity to rest, transform and opacity only. Never pressed again for a date already gone.

### The Gauge
A score is always ten notches, 0.5 by 1rem, ink when on and white with a Field Grey border when off, followed by « 7 sur 10 » in typed bold.

### Motion
Controls ease over 160ms on `cubic-bezier(0.16, 1, 0.3, 1)` (colour, border, the drawer chevron). The two signature moments are the filed card (320ms) and the stamp (300ms). Under `prefers-reduced-motion`, every animation and transition collapses to 1ms and lands on its final state.

## Do's and Don'ts

### Do:
- **Do** give each kind of information its card colour and keep it: pink excludes, blue prefers, yellow is the person’s own words, green is Claude’s text.
- **Do** set text on a card in the card’s own ink for secondary lines, and keep primary text in Ink Black.
- **Do** use School Ink Blue for every action and only for actions: buttons, links, focus, selection.
- **Do** keep stamp red for the first-send date and destructive actions, and error red for errors, each with its own token.
- **Do** use 2px corners and 2px borders on every control and container.
- **Do** set counts, dates, seconds and scores small in Courier Prime with tabular figures.
- **Do** name progress by block and give the time left in minutes.
- **Do** build every control on a native input and make the page work without JavaScript first.
- **Do** add every new colour pairing to the contrast test (4.5:1 text, 3:1 boundaries).
- **Do** write French copy in the vouvoiement with curly apostrophes (’) and French quotes (« »), and keep CSS class names in English BEM.

### Don't:
- **Don't** add a `box-shadow`, gradient or paper texture to anything.
- **Don't** use a cream or warm ground; the ground stays cold, blue-tinted white.
- **Don't** show step numbers or a percentage progress bar.
- **Don't** use a card colour as decoration or for a kind of information it does not hold.
- **Don't** set headings, labels or prose in Courier Prime.
- **Don't** put a destructive action next to a primary button.
- **Don't** round anything past 2px; native radios and the busy spinner are the only circles.
- **Don't** use an em dash anywhere in the interface copy.
- **Don't** load fonts from a CDN or through `@import`.
