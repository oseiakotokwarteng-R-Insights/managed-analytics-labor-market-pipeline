// Small helpers used by every view: loading data, formatting numbers, and hand-built SVG charts (no libraries).

const cache = new Map();
export function getJSON(name) {
  if (!cache.has(name)) {
    cache.set(name, fetch(`data/${name}`, { cache: 'no-cache' }).then(r => {
      if (!r.ok) throw new Error(`${name}: ${r.status}`);
      return r.json();
    }));
  }
  return cache.get(name);
}

export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
export const n0 = v => (v == null ? null : nf0.format(v));
export const usd = v => (v == null ? null : '$' + nf0.format(v));
export const pct1 = v => (v == null ? null : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}%`);
export const plain1 = v => (v == null ? null : `${v.toFixed(1)}%`);

// A number BLS hid is shown as a word, never as zero.
export const HIDDEN = '<span class="hidden-val" title="BLS hides some numbers to protect the privacy of individual businesses">hidden</span>';
export const show = (v, fmt) => (v == null ? HIDDEN : fmt(v));

export function change(now, before) {
  if (now == null || before == null || before === 0) return null;
  return (100 * (now - before)) / before;
}

export function deltaHTML(pct, suffix = 'vs the year before') {
  if (pct == null) return `<span class="delta">no change to show (a number is hidden or missing)</span>`;
  const cls = pct > 0 ? 'up' : pct < 0 ? 'down' : '';
  return `<span class="delta"><span class="${cls}">${pct1(pct)}</span> ${suffix}</span>`;
}

export const dateShort = iso => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
};

export const countyName = title => String(title || '').split(',')[0];

export const METRICS = {
  jobs: { key: 'employment', label: 'Jobs', unit: 'jobs', fmt: n0 },
  wage: { key: 'wage', label: 'Average weekly wage', unit: 'dollars a week', fmt: usd },
  businesses: { key: 'establishments', label: 'Businesses', unit: 'businesses', fmt: n0 },
};

// ---------------------------------------------------------------------------------------------- charts
function niceStep(range, ticks) {
  const raw = range / ticks;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}

/**
 * A line chart over the years. `series` is a list of { name, color, values, hidden } lined up with `years`.
 * A null value breaks the line. A year BLS hid is marked along the bottom, so a gap never looks like a zero.
 */
export function lineChart({ years, series, fmt, label }) {
  const all = series.flatMap(s => s.values).filter(v => v != null);
  if (!all.length) return `<p class="empty">Every number for this view is hidden by BLS, so there's nothing to draw.</p>`;
  const W = 720, H = 300, m = { l: 70, r: 18, t: 14, b: 44 };
  let lo = Math.min(...all), hi = Math.max(...all);
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.12;
  lo = Math.max(0, lo - pad); hi += pad;
  const step = niceStep(hi - lo, 4);
  const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
  const x = i => m.l + (years.length === 1 ? (W - m.l - m.r) / 2 : (i * (W - m.l - m.r)) / (years.length - 1));
  const y = v => m.t + (H - m.t - m.b) * (1 - (v - y0) / (y1 - y0));

  let g = '';
  for (let t = y0; t <= y1 + step / 2; t += step) {
    g += `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/><text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${esc(fmt(Math.round(t)))}</text>`;
  }
  years.forEach((yr, i) => { g += `<text x="${x(i)}" y="${H - m.b + 18}" text-anchor="middle">${yr}</text>`; });

  for (const s of series) {
    let path = '', pen = false, dots = '';
    s.values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      path += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
      pen = true;
      dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4.5" style="fill:${s.color}"><title>${esc(s.name)}, ${years[i]}: ${esc(fmt(v))}</title></circle>`;
    });
    g += `<path d="${path}" style="fill:none;stroke:${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
    s.hidden?.forEach((h, i) => {
      if (h) g += `<text x="${x(i)}" y="${H - m.b + 34}" text-anchor="middle" style="fill:${s.color}">hidden<title>${esc(s.name)}, ${years[i]}: hidden by BLS</title></text>`;
    });
  }
  const summary = series.map(s => `${s.name}: ${s.values.map((v, i) => `${years[i]} ${v == null ? 'hidden' : fmt(v)}`).join(', ')}`).join('. ');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}. ${esc(summary)}">${g}</svg>`;
}

export function legend(series) {
  return `<div class="legend">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
}

/** A small table of the same numbers a chart draws, for people who'd rather read them (and for screen readers). */
export function dataTable({ years, series, fmt }) {
  const head = `<tr><th>Year</th>${series.map(s => `<th class="num">${esc(s.name)}</th>`).join('')}</tr>`;
  const body = years.map((yr, i) => `<tr><td>${yr}</td>${series.map(s => `<td class="num">${show(s.values[i], fmt)}</td>`).join('')}</tr>`).join('');
  return `<details class="data-table"><summary>Show the numbers</summary><div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div></details>`;
}

export const options = (items, selected) => items.map(([value, text]) => `<option value="${esc(value)}"${String(value) === String(selected) ? ' selected' : ''}>${esc(text)}</option>`).join('');
