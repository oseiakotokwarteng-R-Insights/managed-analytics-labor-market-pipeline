// How much to trust the numbers: what was checked, what the checks found, and exactly which files the data came from.
import { dateShort, esc, getJSON, n0 } from '../lib.js';

const friendly = name => esc(String(name).replace(/_/g, ' '));
const chipFor = c => (c.passed ? '<span class="chip good">passed</span>' : c.level === 'error' ? '<span class="chip bad">failed</span>' : '<span class="chip warn">worth a look</span>');
const mb = bytes => (bytes == null ? '' : `${(bytes / 1048576).toFixed(0)} MB`);

function checkList(items) {
  if (!items.length) return '<p class="empty">No results to show.</p>';
  return `<ul class="checklist">${items.map(c => `<li>${chipFor(c)}<div class="what"><div class="name">${friendly(c.name)}</div>${c.detail && c.detail !== 'ok' ? `<div class="detail">${esc(c.detail)}</div>` : ''}</div></li>`).join('')}</ul>`;
}

export async function render(root, ctx) {
  const { meta } = ctx;
  const data = await getJSON('checks.json');
  const run = meta.last_run;
  const checks = data.checks ?? [];
  const warnings = checks.filter(c => !c.passed);
  const sorted = [...warnings, ...checks.filter(c => c.passed)];
  const states = Object.fromEntries(meta.states.map(s => [s.fips, s.name]));

  let cbpHTML = '';
  if (meta.has_cbp) {
    try {
      const cbp = await getJSON('cbp.json');
      const rows = cbp.state_ratios.map(r => `<tr><td>${esc(states[r.state] ?? r.state)}</td><td>${r.year}</td><td class="num">${n0(r.qcew_employment)}</td><td class="num">${n0(r.cbp_employment)}</td><td class="num">${r.ratio == null ? '' : r.ratio.toFixed(3)}</td></tr>`).join('');
      cbpHTML = `<section class="card stack" aria-labelledby="cbp-h">
        <h2 id="cbp-h">A second source agrees (or doesn't)</h2>
        <p class="sub">The Census Bureau counts jobs separately, in a different way, in a program called County Business Patterns. If the BLS numbers are right, the two should land close together, not match exactly.</p>
        ${checkList(cbp.results)}
        <div class="table-wrap"><table><thead><tr><th>State</th><th>Year</th><th class="num">BLS jobs</th><th class="num">Census jobs</th><th class="num">Census divided by BLS</th></tr></thead><tbody>${rows}</tbody></table></div>
      </section>`;
    } catch { cbpHTML = ''; }
  }

  const runLine = run
    ? `The last run finished ${dateShort(run.finished_at)} and ${run.status === 'success' ? 'loaded the data' : 'did <b>not</b> load the data (' + esc(run.status) + ')'}. It added ${n0(run.rows_inserted ?? 0)} rows, changed ${n0(run.rows_changed ?? 0)}, and left ${n0(run.rows_unchanged ?? 0)} as they were${run.rows_removed ? `, and set aside ${n0(run.rows_removed)} that BLS stopped publishing` : ''}.`
    : 'No run is recorded in the database yet.';

  const sourceRows = (meta.sources ?? []).map(s => `<tr><td>${s.year}</td><td class="num">${n0(s.rows_scanned)}</td><td class="num">${n0(s.rows_kept)}</td><td>${esc(mb(s.size_bytes))}</td><td><code title="${esc(s.sha256)}">${esc(String(s.sha256 ?? '').slice(0, 12))}</code></td></tr>`).join('');
  const history = (data.history ?? []).map(h => `<tr><td>${h.run_id}</td><td>${esc(dateShort(h.finished_at || h.started_at))}</td><td>${esc(h.status)}</td><td class="num">${n0(h.rows_inserted ?? 0)}</td><td class="num">${n0(h.rows_changed ?? 0)}</td><td class="num">${n0(h.validation_warnings ?? 0)}</td></tr>`).join('');

  root.innerHTML = `
    <div class="page-head">
      <h1>Data checks</h1>
      <p class="sub">Before anything is saved, the pipeline runs a set of checks on it. This page shows what they found, so you can decide how far to trust the numbers.</p>
    </div>
    <div class="stack">
      <section class="card stack" aria-labelledby="run-h">
        <h2 id="run-h">The last run</h2>
        <p>${runLine}</p>
        <p class="sub">${checks.length - warnings.length} of ${checks.length} checks passed${warnings.length ? `, and ${warnings.length} ${warnings.length === 1 ? 'is' : 'are'} worth a look` : ''}. A check that stops the load would be marked as failed. The others are warnings I look into, like a year where the share of hidden values jumped.</p>
        ${checkList(sorted)}
      </section>
      ${cbpHTML}
      <div class="two">
        <section class="card stack" aria-labelledby="src-h">
          <h2 id="src-h">Where the data came from</h2>
          <p class="sub">One file per year from BLS. The short code is a fingerprint (SHA-256): if you download the same file, you get the same code.</p>
          <div class="table-wrap"><table><thead><tr><th>Year</th><th class="num">Rows read</th><th class="num">Rows kept</th><th>Size</th><th>Fingerprint</th></tr></thead><tbody>${sourceRows}</tbody></table></div>
        </section>
        <section class="card stack" aria-labelledby="hist-h">
          <h2 id="hist-h">Recent runs</h2>
          <div class="table-wrap"><table><thead><tr><th>Run</th><th>Finished</th><th>Result</th><th class="num">Added</th><th class="num">Changed</th><th class="num">Warnings</th></tr></thead><tbody>${history}</tbody></table></div>
        </section>
      </div>
      <section class="card stack" aria-labelledby="about-h">
        <h2 id="about-h">About this data</h2>
        <p>The numbers come from the Quarterly Census of Employment and Wages (QCEW), which the U.S. Bureau of Labor Statistics publishes for free. It counts jobs, businesses and wages from employers' unemployment insurance records, county by county.</p>
        <p>This page shows private-sector jobs only, because government jobs are counted and paid differently. Jobs are the average over the year. We reshape and check the numbers, but we don't estimate or adjust any of them. It counts jobs, not people, so it can't tell you where workers live or who they are.</p>
        <p>This is a sample project from <a href="https://managedanalytics.co" target="_blank" rel="noopener">Managed Analytics</a>, built to show how we work. The code is open, and the pipeline behind it is on <a href="https://github.com/oseiakotokwarteng-R-Insights/managed-analytics-labor-market-pipeline" target="_blank" rel="noopener">GitHub</a>.</p>
      </section>
    </div>`;
}
