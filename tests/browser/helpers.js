// Shared helpers for the browser tests.
const fs = require('fs');
const path = require('path');

const BASE = 'http://127.0.0.1:8765';
const STUB = fs.readFileSync(path.join(__dirname, 'maps-stub.js'), 'utf8');
const OFFICER = { username: 'officer', password: 'officer-password-123' };

async function api(method, url, body, token) {
  const response = await fetch(BASE + url, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: response.status, body: await response.json().catch(() => ({})) };
}

const officerToken = async () => (await api('POST', '/api/auth/login', OFFICER)).body.access_token;

async function newCitizen(email = `rider_${Math.random().toString(36).slice(2, 8)}@example.com`) {
  const created = await api('POST', '/api/auth/register', { email, password: 'citizen-password-1' });
  return { name: created.body.user.username, email, token: created.body.access_token };
}

// A page with the Google Maps stand-in; failures in page scripts are collected for a final check.
async function stubbedPage(browser, options = {}, errors = []) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, ...options });
  await context.addInitScript(STUB);
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  page.on('dialog', dialog => dialog.accept());
  return page;
}

const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Sidebar items may carry a count badge, so match on how the name starts.
const go = (page, name) => page.locator('.sidebar').getByRole('button', { name: new RegExp(`^${escape(name)}`) }).first().click();
// A label's text includes its options, so match on how the label starts.
const field = (page, text) => page.locator('label', { hasText: new RegExp(`^${escape(text)}`) }).first();
const toastText = page => page.locator('.toast').first();

// Sideways scrolling of the page, or inside the commuter panel or a card; tables scroll on purpose.
const overflowAt = page => page.evaluate(width => {
  if (document.documentElement.scrollWidth > width + 1) return `page ${document.documentElement.scrollWidth}px`;
  for (const el of document.querySelectorAll('.commuter-workspace-panel, .page-wrap, .card')) {
    const style = getComputedStyle(el).overflowX;
    if (el.scrollWidth > el.clientWidth + 1 && style !== 'visible' && style !== 'hidden') return `${el.className.split(' ')[0]} ${el.scrollWidth}px`;
  }
  return '';
}, page.viewportSize().width);

module.exports = { BASE, OFFICER, api, officerToken, newCitizen, stubbedPage, go, field, toastText, overflowAt };
