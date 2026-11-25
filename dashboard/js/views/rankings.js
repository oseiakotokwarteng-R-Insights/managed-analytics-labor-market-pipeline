// Every county side by side for one year. Sortable and searchable.
import { go } from '../app.js';
import { HIDDEN, change, countyName, esc, n0, options, show, usd } from '../lib.js';

const COLUMNS = [
  ['name', 'County', false],
  ['state', 'State', false],
  ['jobs', 'Jobs', true],
  ['change', 'Change in jobs', true],
  ['wage', 'Weekly wage', true],
  ['businesses', 'Businesses', true],
];

export async function render(root, ctx, route) {
  const { meta, counties } = ctx;
  const { years } = counties;
  const last = years.length - 1;
  const p = route.params;
  const yi = years.indexOf(Number(p.y)) >= 0 ? years.indexOf(Number(p.y)) : last;
  const stateFilter = meta.states.some(s => s.fips === p.state) ? p.state : '';
  const sort = COLUMNS.some(c => c[0] === p.sort) ? p.sort : 'jobs';
  const dir = p.dir === 'asc' ? 'asc' : sort === 'name' || sort === 'state' ? 'asc' : 'desc';
  const stateName = fips => meta.states.find(s => s.fips === fips)?.name ?? fips;

  const rows = counties.counties
    .filter(c => !stateFilter || c.state === stateFilter)
    .map(c => ({
      c,
      name: countyName(c.title),
      state: stateName(c.state),
      jobs: c.employment[yi],
      change: yi > 0 ? change(c.employment[yi], c.employment[yi - 1]) : null,
      wage: c.wage[yi],
      businesses: c.establishments[yi],
    }));
  const sign = dir === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    const x = a[sort], y = b[sort];
    if (x == null && y == null) return a.name.localeCompare(b.name);
    if (x == null) return 1; // hidden numbers always sink to the bottom
    if (y == null) return -1;
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * sign || a.name.localeCompare(b.name);
  });

  const head = COLUMNS.map(([key, label, num]) => {
    const sorted = key === sort ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';
    return `<th class="${num ? 'num' : ''}" aria-sort="${sorted}"><button type="button" data-sort="${key}">${label}</button></th>`;
  }).join('');
  const body = rows.map(r => `<tr data-name="${esc((r.name + ' ' + r.state).toLowerCase())}">
      <td><a href="#/counties/${r.c.fips}">${esc(r.name)}</a></td><td>${esc(r.state)}</td>
      <td class="num">${show(r.jobs, n0)}</td>
      <td class="num">${r.change == null ? '<span class="hidden-val">n/a</span>' : `<span class="${r.change > 0 ? 'up' : r.change < 0 ? 'down' : ''}">${r.change > 0 ? '+' : r.change < 0 ? '−' : ''}${Math.abs(r.change).toFixed(1)}%</span>`}</td>
      <td class="num">${show(r.wage, usd)}</td><td class="num">${show(r.businesses, n0)}</td></tr>`).join('');

  root.innerHTML = `
    <div class="page-head">
      <h1>Rankings</h1>
      <p class="sub">Every county for ${years[yi]}, private-sector only. Click a column heading to sort. Click a county to see it in detail.</p>
    </div>
    <div class="stack">
      <div class="controls">
        <div class="field"><label for="year">Year</label><select id="year">${options(years.map(y => [y, y]), years[yi])}</select></div>
        <div class="field"><label for="state">State</label><select id="state">${options([['', 'All states']].concat(meta.states.map(s => [s.fips, s.name])), stateFilter)}</select></div>
        <div class="field"><label for="q">Find a county</label><input id="q" type="search" placeholder="Start typing a name" autocomplete="off"></div>
      </div>
      <div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody id="rows">${body}</tbody></table></div>
      <p id="none" class="empty" hidden>No county matches that.</p>
      <p class="note"><b>Reading this.</b> Jobs are the average over the year. A number shows as ${HIDDEN} when BLS hides it to protect the privacy of individual businesses. Those counties sort to the bottom, and the change column is empty when either year's number is hidden.</p>
    </div>`;

  const keep = { y: yi === last ? '' : years[yi], state: stateFilter, sort: p.sort, dir: p.dir };
  root.querySelector('#year').addEventListener('change', e => go('rankings', '', { ...keep, y: Number(e.target.value) === years[last] ? '' : e.target.value }));
  root.querySelector('#state').addEventListener('change', e => go('rankings', '', { ...keep, state: e.target.value }));
  root.querySelectorAll('[data-sort]').forEach(b => b.addEventListener('click', () => {
    const key = b.dataset.sort;
    const nextDir = key === sort ? (dir === 'asc' ? 'desc' : 'asc') : key === 'name' || key === 'state' ? 'asc' : 'desc';
    go('rankings', '', { ...keep, sort: key, dir: nextDir });
  }));
  const tbody = root.querySelector('#rows'), none = root.querySelector('#none');
  root.querySelector('#q').addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    let shown = 0;
    for (const tr of tbody.rows) { const hit = !q || tr.dataset.name.includes(q); tr.hidden = !hit; if (hit) shown++; }
    none.hidden = shown > 0;
  });
}
