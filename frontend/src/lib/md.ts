import { marked } from 'marked';

marked.setOptions({ breaks: true, gfm: true });

export function md(text: string): string {
  return marked.parse(text || '', { async: false }) as string;
}

// Render markdown, then turn inline [p.N] citations into clickable clay chips.
// The chips are plain <button class="cite" data-pn="N"> handled by a delegated
// click on the container (digits only → safe to inject).
export function mdCited(text: string): string {
  const html = md(text);
  return html.replace(/\[p\.(\d+)\]/g, (_m, n) =>
    `<button type="button" class="cite" data-pn="${n}">p.${n}</button>`
  );
}

// Strip leaked citation-noise digit runs from a chunk of rendered HTML text.
// The LLM sometimes emits source markers as raw un-bracketed digit clusters
// (or several jammed `[5][1][6]` → "516") that the backend's `[N]`→`[p.X]`
// rewrite never catches, so they surface as garbage glued to word/line ends:
//   "Supplier Name 516", "...steps655", "216" on its own line, etc.
//
// PRECISION over aggression — a space-separated digit run is INDISTINGUISHABLE
// from a real number ("70,000" "10000" "extension 4500" "Currency 2" "2 days"
// "4 digits" "31/12/49"), so we deliberately DO NOT touch those. We only strip
// the two unambiguous citation-noise shapes:
//   (a) a digit run of >=7 digits at a boundary — no real page/quantity in
//       these runbooks is that long; this is the giveaway for jammed markers
//       like "645545172336412485".
//   (b) a digit run (>=2 digits) WELDED DIRECTLY onto the tail of an alphabetic
//       word with NO separating space ("Supplier Name516", "steps655") — a real
//       number never glues onto a word, so this is safe.
// Both only at a clause/line boundary (end-of-string, or before a newline /
// closing tag / sentence punctuation) and not inside a larger number.
function stripCiteNoise(s: string): string {
  // (a) very long orphaned run (>=7 digits), not part of a larger number.
  //     left side: must not be a digit / number separator (, . / : -)
  s = s.replace(/(^|[^\d.,/:\-])(\d{7,})(?=\s*(?:<|$|[.,;:!?)\]]?\s*\n|[.,;:!?)\]]?$))/g,
    (_m, pre) => pre);
  // (b) digit run welded to the end of an alphabetic word (no space), at a
  //     boundary. The LETTER immediately before the digits is what makes this
  //     safe — "2 days" / "Currency 2" / "10000" all have a space or no letter.
  s = s.replace(/([A-Za-zက-႟])(\d{2,})(?=\s*(?:<|$|[.,;:!?)\]]?\s*\n|[.,;:!?)\]]?$))/g,
    (_m, ch) => ch);
  return s;
}

// Apply stripCiteNoise to text nodes only, leaving the contents of <code> /
// <pre> blocks untouched (SQL queries are full of legitimate numbers).
function stripCiteNoiseOutsideCode(html: string): string {
  // Split on code/pre regions; transform only the segments between them.
  const parts = html.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/gi);
  for (let i = 0; i < parts.length; i++) {
    // even indices = outside code, odd indices = the captured code block
    if (i % 2 === 0) parts[i] = stripCiteNoise(parts[i]);
  }
  return parts.join('');
}

// Render markdown, then make BOTH inline citation forms clickable superscript
// chips: `[p.N]` (page-number form) → data-pn, and bare `[N]` (source-number
// form the model now emits) → data-n. Digits only → safe to inject. The host
// attaches a delegated click handler that opens the matching source. Finally,
// strip leaked un-bracketed digit-cluster citation noise (outside code).
export function mdCiteAll(text: string): string {
  let html = md(text);
  // page-number form first (so the later bare-[N] pass can't touch its digits)
  html = html.replace(/\[p\.(\d+)\]/g,
    (_m, n) => `<sup class="cite" data-pn="${n}">${n}</sup>`);
  // bare source-number form [1] / [2] …
  html = html.replace(/\[(\d+)\]/g,
    (_m, n) => `<sup class="cite" data-n="${n}">${n}</sup>`);
  // garbage leaked digit runs (after real citations are already wrapped in tags)
  html = stripCiteNoiseOutsideCode(html);
  return html;
}
