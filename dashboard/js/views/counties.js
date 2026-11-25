// One county at a time: jobs, wages and businesses over the years, and what it's made of.
import { go } from '../app.js';
import { METRICS, change, countyName, dataTable, deltaHTML, esc, getJSON, legend, lineChart, n0, options, plain1, show, usd, HIDDEN } from '../lib.js';

const COLORS = ['var(--series-a)', 'var(--series-b)'];

export async function render(root, ctx, route) {
  const { meta, counties } = ctx;
  const { years } = counties;
  const list = counties.counties;
  const p = route.params;
  const last = years.length - 1;
  const stateName = fips => meta.states.find(s => s.fips === fips)?.name ?? fips;

  const stateFilter = meta.states.some(s => s.fips === p.state) ? p.state : '';
  const inState = list.filter(c => !stateFilter || c.state === stateFilter);
  const biggest = [...inState].sort((a, b) => (b.employment[last] ?? -1) - (a.employment[last] ?? -1))[0];
  const county = list.find(c => c.fips === route.arg && (!stateFilter || c.state === stateFilter)) || biggest || list[0];
  if (!county) { root.innerHTML = '<p class="empty">There are no counties in the data yet.</p>'; return; }

  const metric = METRICS[p.m] ? p.m : 'jobs';
  const M = METRICS[metric];
  const other = list.find(c => c.fips === p.cmp && c.fips !== county.fips) || null;
  const yearIdx = Math.max(0, Math.min(last, years.indexOf(Number(p.y)) >= 0 ? years.indexOf(Number(p.y)) : last));

  const keep = { state: stateFilter, m: metric === 'jobs' ? '' : metric, cmp: other?.fips, y: yearIdx === last ? '' : years[yearIdx] };
  const alpha = [...inState].sort((a, b) => a.title.localeCompare(b.title));
  const compareOptions = [['', 'No one']].concat(list.filter(c => c.fips !== county.fips).sort((a, b) => a.title.localeCompare(b.title)).map(c => [c.fips, c.title]));

  const kpi = (label, key, fmt) => {
    const now = county[key][last], before = county[key][last - 1];
    return `<div class="card kpi"><div class="label">${label}, ${years[last]}</div><div class="value">${show(now, fmt)}</div>${deltaHTML(change(now, before), `vs ${years[last - 1] ?? 'the year before'}`)}</div>`;
  };

  const series = [{ name: countyName(county.title), color: COLORS[0], values: county[M.key], hidden: county.hidden }];
  if (other) series.push({ name: countyName(other.title), color: COLORS[1], values: other[M.key], hidden: other.hidden });

  root.innerHTML = `
    <div class="page-head">
      <h1>${esc(county.title)}</h1>
      <p class="sub">Private-sector jobs, pay and businesses, ${years[0]} to ${years[last]}. Pick a county below, or open <a href="#/rankings">Rankings</a> to compare them all.</p>
    </div>
    <div class="stack">
      <div class="controls">
        <div class="field"><label for="state">State</label><select id="state">${options([['', 'All states']].concat(meta.states.map(s => [s.fips, s.name])), stateFilter)}</select></div>
        <div class="field"><label for="county">County</label><select id="county">${options(alpha.map(c => [c.fips, countyName(c.title) + (stateFilter ? '' : ', ' + stateName(c.state))]), county.fips)}</select></div>
        <div class="field"><label for="cmp">Compare with</label><select id="cmp">${options(compareOptions.map(([v, t]) => [v, v ? t : t]), other?.fips ?? '')}</select></div>
      </div>
      <div class="grid3">
        ${kpi('Jobs', 'employment', n0)}
        ${kpi('Average weekly wage', 'wage', usd)}
        ${kpi('Businesses', 'establishments', n0)}
      </div>
      <section class="card" aria-labelledby="trend-h">
        <div class="controls" style="justify-content:space-between;margin-bottom:8px">
          <h2 id="trend-h">${M.label} over the years</h2>
          <div class="seg" role="group" aria-label="What to chart">
            ${Object.entries(METRICS).map(([k, v]) => `<button type="button" data-metric="${k}" aria-pressed="${k === metric}">${v.label}</button>`).join('')}
          </div>
        </div>
        ${legend(series)}
        ${lineChart({ years, series, fmt: M.fmt, label: `${M.label} over the years` })}
        ${dataTable({ years, series, fmt: M.fmt })}
      </section>
      <section class="card" aria-labelledby="sec-h">
        <div class="controls" style="justify-content:space-between;margin-bottom:10px">
          <h2 id="sec-h">What the jobs are in</h2>
          <div class="field"><label for="year">Year</label><select id="year">${options(years.map(y => [y, y]), years[yearIdx])}</select></div>
        </div>
        <div id="sectors"><p class="loading">Loading the industries&hellip;</p></div>
      </section>
      <p class="note"><b>About hidden numbers.</b> To protect the privacy of individual businesses, BLS hides a number when only a few employers are behind it. We show that as ${HIDDEN}, never as a zero, and we don't estimate it. The industries below can add up to less than the county's total for the same reason.</p>
    </div>`;

  const redo = patch => go('counties', patch.county ?? county.fips, { ...keep, ...patch.params });
  const on = (id, fn) => root.querySelector(id).addEventListener('change', fn);
  on('#state', e => go('counties', '', { ...keep, state: e.target.value, cmp: '' }));
  on('#county', e => redo({ county: e.target.value }));
  on('#cmp', e => redo({ params: { cmp: e.target.value } }));
  on('#year', e => redo({ params: { y: Number(e.target.value) === years[last] ? '' : e.target.value } }));
  root.querySelectorAll('[data-metric]').forEach(b => b.addEventListener('click', () => {
    go('counties', county.fips, { ...keep, m: b.dataset.metric === 'jobs' ? '' : b.dataset.metric });
  }));

  drawSectors(root.querySelector('#sectors'), county, years, yearIdx);
}

async function drawSectors(box, county, years, idx) {
  let data;
  try { data = await getJSON(`sectors/${county.fips}.json`); } catch { box.innerHTML = '<p class="empty">No industry detail is available for this county.</p>'; return; }
  const total = county.employment[idx];
  const rows = data.sectors
    .map(s => ({ ...s, jobs: s.employment[idx], before: idx > 0 ? s.employment[idx - 1] : null, wageNow: s.wage[idx], isHidden: !!s.hidden[idx] }))
    .sort((a, b) => (b.jobs ?? -1) - (a.jobs ?? -1) || a.title.localeCompare(b.title));
  if (!rows.length) { box.innerHTML = '<p class="empty">No industry detail is available for this county.</p>'; return; }
  const body = rows.map(r => {
    const share = r.jobs != null && total ? (100 * r.jobs) / total : null;
    const ch = change(r.jobs, r.before);
    return `<tr>
      <td>${esc(r.title)}</td>
      <td class="num">${show(r.jobs, n0)}</td>
      <td class="num">${share == null ? HIDDEN : plain1(share)}${share == null ? '' : `<span class="bar" aria-hidden="true"><i style="width:${Math.min(100, share).toFixed(1)}%"></i></span>`}</td>
      <td class="num">${show(r.wageNow, usd)}</td>
      <td class="num">${ch == null ? '<span class="hidden-val">n/a</span>' : `<span class="${ch > 0 ? 'up' : ch < 0 ? 'down' : ''}">${ch > 0 ? '+' : ch < 0 ? '−' : ''}${Math.abs(ch).toFixed(1)}%</span>`}</td>
    </tr>`;
  }).join('');
  box.innerHTML = `<div class="table-wrap"><table>
    <caption class="sr" style="position:absolute;left:-999px">Jobs by industry in ${esc(county.title)}, ${years[idx]}</caption>
    <thead><tr><th>Industry</th><th class="num">Jobs</th><th class="num">Share of county jobs</th><th class="num">Weekly wage</th><th class="num">Change in jobs</th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}
