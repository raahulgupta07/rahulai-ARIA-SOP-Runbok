// Humanize raw SOP filenames + clean extracted text for display.

export type DocName = { title: string; code: string; category: string; raw: string };

// "SOP_IT_CMHL_AMS_GLDCENTRAL_028_CAR Parameter Definition Mass Input.pdf"
//   -> { title:"CAR Parameter Definition Mass Input", code:"SOP-028", category:"GLDCENTRAL" }
export function parseDocName(raw: string): DocName {
  const base = (raw || '').replace(/\.(pdf|png|jpe?g)$/i, '');
  let title = base, code = '', category = '';

  // match a trailing numeric id segment then the human title:  ..._<CATEGORY>_<NNN>_<Title>
  const m = base.match(/^(SOP[_-].*?)_([A-Z0-9]+)_(\d{2,4})_(.+)$/);
  if (m) {
    category = m[2];
    code = `SOP-${m[3]}`;
    title = m[4];
  } else {
    // fallback: take everything after the last _<digits>_
    const m2 = base.match(/_(\d{2,4})_(.+)$/);
    if (m2) { code = `SOP-${m2[1]}`; title = m2[2]; }
  }
  title = title.replace(/[_]+/g, ' ').trim();
  return { title: title || base, code, category, raw };
}

// Tidy a raw extracted page text for readable display.
export function cleanText(s: string): string {
  if (!s) return '';
  return s
    .split('\n')
    // drop "0 | P a g e" / "P a g e" footer artifacts
    .filter((ln) => !/^\s*\d*\s*\|?\s*P\s*a\s*g\s*e\s*$/i.test(ln))
    .join('\n')
    // collapse long dotted TOC leaders
    .replace(/\.{4,}/g, ' …… ')
    // collapse 3+ blank lines to one blank line
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
