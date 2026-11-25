// The page shell: loads the two files every view needs, reads the address (#/view/thing?option=value), and draws the view.
import { dateShort, esc, getJSON } from './lib.js';
import * as counties from './views/counties.js';
import * as rankings from './views/rankings.js';
import * as states from './views/states.js';
import * as checks from './views/checks.js';

const views = { counties, rankings, states, checks };
const root = document.getElementById('main');
const ctx = { meta: null, counties: null };

export function parseHash() {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const [name = '', arg = ''] = path.split('/');
  return { name: views[name] ? name : 'counties', arg, params: Object.fromEntries(new URLSearchParams(query)) };
}

/** Change the address, which redraws the view. Keeps keyboard focus on the control that was used. */
export function go(name, arg = '', params = {}) {
  const clean = Object.entries(params).filter(([, v]) => v !== '' && v != null);
  const query = clean.length ? '?' + new URLSearchParams(clean).toString() : '';
  const next = `#/${name}${arg ? '/' + arg : ''}${query}`;
  const keep = document.activeElement?.id;
  if (next === location.hash) return draw(keep);
  location.hash = next;
  window.__keepFocus = keep;
}

async function draw(keepFocusId) {
  const route = parseHash();
  document.querySelectorAll('#nav a').forEach(a => { if (a.dataset.route === route.name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  try {
    await views[route.name].render(root, ctx, route);
  } catch (err) {
    root.innerHTML = `<div class="error"><b>Something went wrong.</b> ${esc(err.message)}</div>`;
  }
  const focusId = keepFocusId || window.__keepFocus;
  window.__keepFocus = null;
  const el = focusId && document.getElementById(focusId);
  if (el) el.focus(); else if (!keepFocusId) { root.focus({ preventScroll: true }); window.scrollTo(0, 0); }
}

function setupTheme() {
  const button = document.getElementById('theme');
  let saved = null;
  try { saved = localStorage.getItem('theme'); } catch { /* private browsing: just follow the device */ }
  if (saved) document.documentElement.dataset.theme = saved;
  button.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* fine */ }
  });
}

async function boot() {
  setupTheme();
  try {
    [ctx.meta, ctx.counties] = await Promise.all([getJSON('meta.json'), getJSON('counties.json')]);
  } catch (err) {
    root.innerHTML = `<div class="error"><b>The data files didn't load.</b> If you're running this from your own computer, serve this folder with <code>python -m http.server</code> and open it from <code>http://localhost:8000</code>, because browsers won't read the files straight from disk. Then run <code>qcew-dashboard</code> first so there is data to show. (${esc(err.message)})</div>`;
    return;
  }
  const m = ctx.meta;
  const updated = dateShort(m.last_run?.finished_at || m.generated_at);
  document.getElementById('foot-data').textContent =
    `Data: U.S. Bureau of Labor Statistics, QCEW, ${m.years[0]} to ${m.latest_year}. ${m.region_name ? m.region_name + '. ' : ''}${updated ? 'Updated ' + updated + '.' : ''}`;
  window.addEventListener('hashchange', () => draw());
  draw();
}

boot();
