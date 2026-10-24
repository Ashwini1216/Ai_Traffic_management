// With no Google keys at all, every map is an OpenStreetMap map and place search uses OpenStreetMap.
// Map tiles are answered locally with a blank tile: OpenStreetMap's tile policy does not allow
// automated browsers to load its tiles, and the tests never use a public service.
const { test, expect } = require('@playwright/test');
const { OFFICER, go, field } = require('./helpers');

const BASE = 'http://127.0.0.1:8766';
const TILE = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');
const errors = [];
const tiles = [];
let page;

test.describe.configure({ mode: 'serial' });

async function newPage(browser) {
  const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1360, height: 900 },
    permissions: ['geolocation'], geolocation: { latitude: 12.9716, longitude: 77.6412, accuracy: 25 } });
  // Only the test server is reachable; map tiles get the blank tile, anything else is refused.
  await context.route(url => !url.href.startsWith(BASE), route => {
    const url = route.request().url();
    if (!url.startsWith('https://tile.openstreetmap.org/')) return route.abort();
    tiles.push(url);
    return route.fulfill({ contentType: 'image/png', body: TILE });
  });
  const opened = await context.newPage();
  opened.on('pageerror', error => errors.push(String(error)));
  return opened;
}

test.beforeAll(async ({ browser }) => {
  page = await newPage(browser);
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: /Continue as a commuter/ }).click();
  await page.getByRole('button', { name: /Create an account/ }).click();
  await page.getByPlaceholder('you@example.com').fill(`osm_${Date.now().toString(36)}@example.com`);
  await page.getByPlaceholder('At least 10 characters').fill('citizen-password-1');
  await page.getByRole('button', { name: /Create commuter account/ }).click();
  await expect(page.getByText('Where are you headed?')).toBeVisible();
});

test.afterAll(async () => { await page.context().close(); });

test('without a Google key the home map is an OpenStreetMap map with attribution', async () => {
  const map = page.locator('#commuter-home-map');
  await expect(map).toHaveClass(/leaflet-container/);
  await expect(page.locator('.google-map-setup-note')).toHaveCount(0);
  await expect(map.locator('.leaflet-control-attribution')).toContainText('OpenStreetMap contributors');
  await expect.poll(() => map.locator('.leaflet-overlay-pane path').count()).toBeGreaterThanOrEqual(8);  // zone circles
  expect(tiles.length).toBeGreaterThan(0);
});

test('a place searched by name is zoomed to and set as the destination', async () => {
  const search = page.locator('.home-search');
  await search.getByRole('textbox').fill('Avalahalli');
  await search.getByRole('button', { name: 'Search' }).click();
  await expect(search.locator('.place-results')).toContainText('Avalahalli, Bengaluru South');
  await expect(search.locator('.place-attribution')).toHaveText('Search results © OpenStreetMap contributors');
  await search.locator('.place-results button').first().click();
  await expect.poll(() => tiles.some(url => /\/15\/\d+\/\d+\.png$/.test(url))).toBe(true);
  // The searched place is marked in the middle of the map, even after the panel beside it changed size.
  const offCentre = async () => {
    const map = await page.locator('#commuter-home-map').boundingBox();
    const marker = await page.locator('#commuter-home-map path[fill="#5b47a8"]').boundingBox();
    return Math.max(Math.abs(marker.x + marker.width / 2 - (map.x + map.width / 2)), Math.abs(marker.y + marker.height / 2 - (map.y + map.height / 2)));
  };
  await expect.poll(offCentre).toBeLessThan(3);
  await search.getByRole('button', { name: 'Set as destination' }).click();
  await expect(page.locator('.pin-picker').nth(1)).toContainText('Avalahalli');
});

test('routes are drawn on the OpenStreetMap map', async () => {
  await go(page, 'Start a journey');
  await field(page, 'Starting point').locator('select').selectOption({ label: '100 Feet Road, Indiranagar' });
  // The destination chosen by search on the home screen is already filled in.
  await expect(field(page, 'Destination').locator('select')).toHaveValue('pin');
  await page.getByRole('button', { name: /Compare routes/ }).click();
  await expect(page.locator('.route-card').first()).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => page.locator('.route-results-map .leaflet-overlay-pane path').count()).toBeGreaterThanOrEqual(1);
});

test('the live position is shown on the OpenStreetMap maps while monitoring', async () => {
  await page.getByRole('button', { name: /Start monitoring this route/ }).click();
  await expect(page.locator('.monitor-status')).toContainText(/On route|Monitoring/, { timeout: 20_000 });
  await expect(page.locator('.route-results-map path[fill="#1a73e8"]')).toHaveCount(2);   // the dot and its accuracy circle
  await expect(page.locator('#commuter-home-map path[fill="#1a73e8"]')).toHaveCount(2);
  await page.getByRole('button', { name: /Stop monitoring/ }).click();
  await expect(page.locator('.route-results-map path[fill="#1a73e8"]')).toHaveCount(0);
});

test('authority maps use OpenStreetMap too', async ({ browser }) => {
  const officer = await newPage(browser);
  await officer.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' });
  await officer.getByRole('button', { name: /Authority sign in/ }).click();
  await officer.getByPlaceholder('Authority email or username').fill(OFFICER.username);
  await officer.getByPlaceholder('Your password').fill(OFFICER.password);
  await officer.getByRole('button', { name: /Sign in securely/ }).click();
  await expect(officer.getByText('City operations')).toBeVisible();
  await go(officer, 'Zone monitor');
  await expect(officer.locator('.zone-map')).toHaveClass(/leaflet-container/);
  await expect.poll(() => officer.locator('.zone-map .leaflet-overlay-pane path').count()).toBeGreaterThanOrEqual(8);
  await officer.context().close();
});

test('only OpenStreetMap tiles over HTTPS were requested, and no script errors occurred', async () => {
  expect(tiles.every(url => url.startsWith('https://tile.openstreetmap.org/'))).toBe(true);
  expect(errors).toEqual([]);
});
