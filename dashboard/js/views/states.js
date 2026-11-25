// Each state's total from BLS next to its counties added up. This is where the "unknown or undefined" bucket shows.
import { go } from '../app.js';
import { HIDDEN, esc, getJSON, n0, options, plain1 } from '../lib.js';

const dash = '<span class="hidden-val" title="A number needed for this is hidden by BLS">n/a</span>';
const num = v => (v == null ? dash : n0(v));

function gap(v) {
  if (v == null) return dash;
  const off = Math.abs(v) >= 1;
  return `<span class="${off ? 'down' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(2)}%</span>`;
}

export async function render(root, ctx, route) {
  const data = await getJSON('states.json');
  const years = data.years;
  const last = years.length - 1;
  const yi = years.indexOf(Number(route.params.y)) >= 0 ? years.indexOf(Number(route.params.y)) : last;
  const year = years[yi];
  const rows = data.rows.filter(r => r.year === year);

  const body = rows.map(r => `<tr>
      <td>${esc(r.name)}</td><td class="num">${num(r.state_total)}</td><td class="num">${num(r.named_counties)}</td>
      <td class="num">${r.unknown_bucket != null ? n0(r.unknown_bucket) : r.unknown_bucket_hidden ? HIDDEN : '<span class="hidden-val">none</span>'}</td>
      <td class="num">${r.bucket_share_pct == null ? dash : plain1(r.bucket_share_pct)}</td>
      <td class="num">${gap(r.gap_without_bucket_pct)}</td><td class="num">${gap(r.gap_with_bucket_pct)}</td></tr>`).join('');

  root.innerHTML = `
    <div class="page-head">
      <h1>States, and the jobs that belong to no county</h1>
      <p class="sub">BLS publishes a total for each state, and it publishes each county. This page adds the counties up and checks them against the state total.</p>
    </div>
    <div class="stack">
      <div class="controls"><div class="field"><label for="year">Year</label><select id="year">${options(years.map(y => [y, y]), year)}</select></div></div>
      <div class="table-wrap"><table>
        <thead><tr><th>State</th><th class="num">State total (BLS)</th><th class="num">Named counties added up</th><th class="num">Unknown or undefined</th><th class="num">That bucket's share</th><th class="num">Gap: counties alone</th><th class="num">Gap: counties plus bucket</th></tr></thead>
        <tbody>${body || '<tr><td colspan="7" class="empty">No state totals for this year.</td></tr>'}</tbody></table></div>
      <div class="note">
        <p><b>What the bucket is.</b> Some jobs can't be placed in a single county, so BLS keeps them in a separate bucket called "unknown or undefined". They count toward the state total, but they aren't in any county's numbers.</p>
        <p style="margin-top:8px"><b>Why it matters.</b> If a state's counties add up to noticeably less than the state total, the bucket is usually why, not a mistake. A county's numbers then leave out a few percent of the state's jobs. The pipeline counts the bucket in its own checks. A gap is left empty (${dash}) when a number it needs is hidden, because guessing would be worse than saying so.</p>
      </div>
    </div>`;

  root.querySelector('#year').addEventListener('change', e => go('states', '', { y: Number(e.target.value) === years[last] ? '' : e.target.value }));
}
