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

// Render markdown, then make BOTH inline citation forms clickable superscript
// chips: `[p.N]` (page-number form) → data-pn, and bare `[N]` (source-number
// form the model now emits) → data-n. Digits only → safe to inject. The host
// attaches a delegated click handler that opens the matching source.
export function mdCiteAll(text: string): string {
  let html = md(text);
  // page-number form first (so the later bare-[N] pass can't touch its digits)
  html = html.replace(/\[p\.(\d+)\]/g,
    (_m, n) => `<sup class="cite" data-pn="${n}">${n}</sup>`);
  // bare source-number form [1] / [2] …
  html = html.replace(/\[(\d+)\]/g,
    (_m, n) => `<sup class="cite" data-n="${n}">${n}</sup>`);
  return html;
}
