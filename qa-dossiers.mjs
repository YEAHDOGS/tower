// qa-dossiers.mjs — 390px + 1440px sweep of all project detail pages (file://).
// Reports: console/page errors, horizontal overflow, broken <img>, missing key sections.
// qa-dossiers.mjs — item-10 dossier QA sweep.
// Opens every projects/*/ detail page (plus castle/* module sub-pages) via
// file:// at 390px and 1440px and reports per page: panel sections present,
// horizontal overflow, console/page errors, broken <img>.
// Known sandbox artifacts to ignore: the favicon's absolute
// https://yeahdogs.github.io/tower/... URL (loads fine in production) and
// demos/*.mp4 requestfailed over file:// (they serve 200 live).
import { execSync } from 'child_process';
import { chromium } from 'playwright-core';
import { fileURLToPath } from 'url';
import path from 'path';

const REPO = process.argv[2] || path.dirname(fileURLToPath(import.meta.url));
// usage: node qa-dossiers.mjs [repo-root]
// (run from a dir where playwright-core resolves, e.g. with a local
// node_modules symlink; the repo root is passed explicitly)
const CHROME = '/opt/meta-chromium/chrome';
const dirs = execSync(`ls ${REPO}/projects`).toString().trim().split('\n')
  .filter(d => {
    try { execSync(`test -f ${REPO}/projects/${d}/index.html`); return true; }
    catch { return false; }
  });

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
for (const [w, h, tag] of [[390, 844, '390'], [1440, 900, '1440']]) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const page = await ctx.newPage();
  console.log(`\n=== ${tag}px ===`);
  for (const d of dirs) {
    const errs = [], broken = [];
    page.on('pageerror', e => errs.push('pageerror: ' + String(e).split('\n')[0]));
    page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 100)); });
    page.on('requestfailed', r => errs.push('reqfail: ' + r.url().slice(-70)));
    try {
      await page.goto(`file://${REPO}/projects/${d}/index.html`, { waitUntil: 'load', timeout: 20000 });
      await page.waitForTimeout(700);
      const ov = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      const imgs = await page.$$eval('img', els => els.map(e => e.getAttribute('src')));
      for (const img of imgs) {
        const ok = await page.evaluate(src => new Promise(res => {
          const i = new Image(); i.onload = () => res(true); i.onerror = () => res(false); i.src = src;
        }), img);
        if (!ok) broken.push(img.slice(-60));
      }
      const secs = await page.$$eval('main .panel > h2', els => els.map(e => e.textContent.trim()));
      const flag = [];
      if (ov > 1) flag.push(`OVERFLOW+${ov}px`);
      if (errs.length) flag.push(`${errs.length}err`);
      if (broken.length) flag.push(`${broken.length}broken-img`);
      console.log(`${d.padEnd(20)} secs:[${secs.join('|')}] ${flag.join(' ')}`);
      if (errs.length) errs.slice(0, 3).forEach(e => console.log(`    ${e}`));
      if (broken.length) broken.slice(0, 4).forEach(b => console.log(`    img: ${b}`));
    } catch (e) {
      console.log(`${d.padEnd(20)} GOTO-FAIL ${String(e).split('\n')[0].slice(0, 80)}`);
    }
  }
  // castle/* module sub-pages (not top-level dirs)
  for (const d of ['castle/app', 'castle/castle-os', 'castle/drive-librarian', 'castle/gateway']) {
    const errs = [];
    page.on('pageerror', e => errs.push('pageerror: ' + String(e).split('\n')[0]));
    try {
      await page.goto(`file://${REPO}/projects/${d}/index.html`, { waitUntil: 'load', timeout: 20000 });
      await page.waitForTimeout(700);
      const ov = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      const secs = await page.$$eval('main .panel > h2', els => els.map(e => e.textContent.trim()));
      const flag = [];
      if (ov > 1) flag.push(`OVERFLOW+${ov}px`);
      if (errs.length) flag.push(`${errs.length}err`);
      console.log(`${d.padEnd(20)} secs:[${secs.join('|')}] ${flag.join(' ')}`);
    } catch (e) {
      console.log(`${d.padEnd(20)} GOTO-FAIL`);
    }
  }
  await ctx.close();
}
await browser.close();
