// Commuter portal, as one signed-in session working through each screen in turn.
const { test, expect } = require('@playwright/test');
const { api, officerToken, stubbedPage, go, field, toastText, overflowAt } = require('./helpers');

const ORIGIN = { latitude: 12.9716, longitude: 77.6412 };     // 100 Feet Road, Indiranagar
const TRINITY = { latitude: 12.9727, longitude: 77.6196 };    // Trinity Circle, M.G. Road
const errors = [];
const EMAIL = `rider_${Date.now().toString(36)}@example.com`;
let page;
let citizen;
let officer;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ browser }) => {
  officer = await officerToken();
  page = await stubbedPage(browser, { permissions: ['geolocation'], geolocation: ORIGIN }, errors);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: /Continue as a commuter/ }).click();
  await page.getByRole('button', { name: /Create an account/ }).click();
  await page.getByPlaceholder('you@example.com').fill(EMAIL);
  await page.getByPlaceholder('At least 10 characters').fill('citizen-password-1');
  await page.getByRole('button', { name: /Create commuter account/ }).click();
  await expect(page.getByText('Where are you headed?')).toBeVisible();
  citizen = await page.evaluate(() => sessionStorage.getItem('flowstateToken'));
});

test.afterAll(async () => { await page.context().close(); });

test('home map shows the zone outlook, level legend and busiest zones', async () => {
  await page.waitForFunction(() => window.__live().circles >= 8);
  await expect(page.locator('.map-legend')).toContainText('Low');
  await expect(page.locator('.map-legend')).toContainText('High');
  await expect(page.locator('.commuter-alert-mini', { hasText: 'Busiest zones now' }).locator('p')).toHaveCount(3);
});

test('a place searched by name is zoomed to and can be set as the destination', async () => {
  const search = page.locator('.home-search');
  await search.getByRole('textbox').fill('Indiranagar');
  await search.getByRole('button', { name: 'Search' }).click();
  await expect(search.locator('.place-results')).toContainText('<img src=x');   // markup in a name stays text
  await search.getByRole('textbox').fill('Avalahalli');
  await search.getByRole('textbox').press('Enter');
  await expect(search.locator('.place-results')).toContainText('Avalahalli, Banashankari');
  await expect(search.locator('.place-attribution')).toHaveText('Search results from Google');
  await search.locator('.place-results button').first().click();
  const view = await page.evaluate(() => {
    const map = window.__maps.filter(m => m.el.id === 'commuter-home-map').pop();
    return { zoom: map.zoom, center: map.center };
  });
  expect(view.zoom).toBe(15);
  expect(view.center.lat).toBeCloseTo(12.9457, 4);
  await search.getByRole('button', { name: 'Set as destination' }).click();
  await expect(page.locator('.pin-picker').nth(1)).toContainText('Avalahalli');
  expect(await page.evaluate(() => window.__xss)).toBeUndefined();
});

test('road forecast shows the level, probabilities, method and confidence', async () => {
  await go(page, 'Road forecast');
  await field(page, 'Area').locator('select').selectOption('Indiranagar');
  await field(page, 'Road or junction').locator('select').selectOption({ label: '100 Feet Road' });
  await field(page, 'When').locator('select').selectOption('3h');
  await page.getByRole('button', { name: /Get forecast/ }).click();
  const card = page.locator('.forecast-card');
  await expect(card.locator('.meter-row')).toHaveCount(3);
  await expect(card).toContainText(/Historical pattern|Hybrid LSTM|Similar roads|Limited-data/);
  await expect(card).toContainText(/confidence/i);
});

test('server validation errors are shown as readable text', async () => {
  await go(page, 'Plan a trip');
  await page.route('**/api/trips', route => (route.request().method() !== 'POST' ? route.continue() : route.fulfill({
    status: 422, contentType: 'application/json',
    body: JSON.stringify({ detail: [{ loc: ['body', 'departure_at'], msg: 'Value error, departure_at must be in the future' }] }),
  })));
  await field(page, 'Starting point').locator('select').selectOption({ label: '100 Feet Road, Indiranagar' });
  await field(page, 'Destination').locator('select').selectOption({ label: 'Trinity Circle, M.G. Road' });
  await page.getByRole('button', { name: /Save planned trip/ }).click();
  await expect(toastText(page)).toHaveText(/departure_at must be in the future/);
  await expect(toastText(page)).not.toContainText('[object Object]');
  await page.unroute('**/api/trips');
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 });
});

test('a planned trip is saved with the roads it passes', async () => {
  await page.getByRole('button', { name: /Save planned trip/ }).click();
  await expect(toastText(page)).toHaveText(/Trip saved: it passes [1-9]\d* monitored road/);
  const trip = page.locator('.trip-item').first();
  await expect(trip).toContainText('100 Feet Road, Indiranagar → Trinity Circle, M.G. Road');
  await expect(trip).toContainText('scheduled');
  await expect(trip.locator('.same-destination')).toContainText(/other travellers plan to arrive within 500 m of your destination/);
});

test('a closure on the trip produces a notification that can be marked read', async () => {
  const notice = await api('POST', '/api/incidents/official', { category: 'road_closure', description: 'Trinity Circle closed for repairs',
    area: 'M.G. Road', road: 'Trinity Circle', ...TRINITY, closure: true }, officer);
  expect(notice.status).toBe(201);
  await page.locator('.trip-item').first().getByRole('button', { name: /Check now/ }).click();
  await expect(toastText(page)).toHaveText(/notification was sent/);
  await go(page, 'Notifications');
  await expect(page.locator('.notification').first()).toContainText(/closure is on your route/i);
  await expect(page.locator('.sidebar .nav-count')).toBeVisible();
  await page.getByRole('button', { name: /Mark all read/ }).click();
  await expect(page.locator('.notification-unread')).toHaveCount(0);
  await expect(page.locator('.sidebar .nav-count')).toHaveCount(0);
  await api('PATCH', `/api/incidents/${notice.body.incident_id}`, { status: 'resolved' }, officer);
});

test('a trip leaving soon brings an advance notice, also as a desktop alert', async () => {
  await page.evaluate(() => {
    window.__desktopAlerts = [];
    window.Notification = class {
      static permission = 'default';
      static async requestPermission() { this.permission = 'granted'; return 'granted'; }
      constructor(title, options) { window.__desktopAlerts.push({ title, body: options?.body }); }
    };
  });
  await go(page, 'Plan a trip');
  await go(page, 'Notifications');
  await page.getByRole('button', { name: /Turn on desktop alerts/ }).click();
  await expect(page.locator('.desktop-alerts')).toContainText('Desktop alerts are on');
  const created = await api('POST', '/api/trips', { origin: '100 Feet Road, Indiranagar', destination: 'Trinity Circle, M.G. Road', ...{
    origin_lat: ORIGIN.latitude, origin_lon: ORIGIN.longitude, destination_lat: TRINITY.latitude, destination_lon: TRINITY.longitude },
    departure_at: new Date(Date.now() + 30 * 60_000).toISOString() }, citizen);
  expect(created.status).toBe(201);
  const run = await api('POST', '/api/jobs/trip_reminders/run', null, officer);
  expect(run.body.details.reminders_sent).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));   // returning to the tab checks at once
  await expect.poll(() => page.evaluate(() => window.__desktopAlerts.map(alert => alert.title))).toEqual(['Before you leave: traffic on your trip']);
  await go(page, 'Plan a trip');
  await go(page, 'Notifications');
  await expect(page.locator('.notification').first()).toContainText(/Your trip from 100 Feet Road, Indiranagar to Trinity Circle, M.G. Road leaves at .*Expected traffic:/);
});

test('route comparison scores routes and gives departure advice', async () => {
  await go(page, 'Start a journey');
  await field(page, 'Starting point').locator('select').selectOption({ label: '100 Feet Road, Indiranagar' });
  await field(page, 'Destination').locator('select').selectOption({ label: 'Silk Board Junction, Electronic City' });
  await page.getByRole('button', { name: /Compare routes/ }).click();
  await expect(page.locator('.route-card')).toHaveCount(2, { timeout: 30_000 });
  const best = page.locator('.route-card').first();
  await expect(best).toContainText(/\/100/);
  await expect(best.locator('.bar-row')).toHaveCount(4);
  await expect(best.locator('.route-choice-main')).toContainText('traffic adds about 2 min');   // Google's traffic delay
  await expect(best.locator('.bar-row').first()).toContainText('Traffic (forecast or live)');
  await expect(best.locator('.road-step').first()).toBeVisible();
  await expect(page.locator('.advice-card, .route-notice').first()).toContainText(/Departure time|forecasts on this route/);
  expect((await page.evaluate(() => window.__live())).polylines).toBeGreaterThanOrEqual(2);
});

test('journey monitoring shows the live position and checks for a better route', async () => {
  await page.getByRole('button', { name: /Start monitoring this route/ }).click();
  await expect(page.locator('.monitor-status')).toContainText(/On route · .*% done · .* km left/, { timeout: 20_000 });
  const liveDots = () => page.evaluate(() => window.__markers.filter(m => m.map && m.opts.title === 'You are here').length);
  await expect.poll(liveDots).toBe(2);   // on the route map and on the main map
  const view = await page.evaluate(() => window.__maps.filter(m => m.el.closest('.route-results-map')).pop().center);
  expect(view.lat).toBeCloseTo(ORIGIN.latitude, 4);   // the map follows the traveller
  await page.getByRole('button', { name: /Check for a better route/ }).click();
  await expect(toastText(page)).toHaveText(/Your route is still the best one from here/);
  await page.getByRole('button', { name: /Stop monitoring/ }).click();
  await expect(page.getByRole('button', { name: /Start monitoring this route/ })).toBeVisible();
  await expect.poll(liveDots).toBe(0);
});

test('an incident report carries the location and no empty strings', async () => {
  await go(page, 'Report an incident');
  await page.getByRole('button', { name: /Attach my current location/ }).click();
  await expect(page.getByText(/shared with this report/)).toBeVisible();
  await page.getByPlaceholder(/What did you observe/).fill('Signal not working at the junction');
  const request = page.waitForRequest(r => r.url().endsWith('/api/incidents') && r.method() === 'POST');
  await page.getByRole('button', { name: /Send report/ }).click();
  const body = JSON.parse((await request).postData());
  expect(body.area).toBeFalsy();
  expect(body.road).toBeFalsy();
  expect(body.latitude).toBe(ORIGIN.latitude);
  await expect(toastText(page)).toHaveText(/Report sent/);
});

test('a matching report nearby is merged as a confirmation', async () => {
  await expect(page.locator('.toast')).toHaveCount(0, { timeout: 10_000 });
  await page.getByRole('button', { name: /Attach my current location/ }).click();
  await expect(page.getByText(/shared with this report/)).toBeVisible();
  await page.getByPlaceholder(/What did you observe/).fill('Same signal, still not working');
  await page.getByRole('button', { name: /Send report/ }).click();
  await expect(toastText(page)).toHaveText(/added as a confirmation/);
});

test('the account page sends an authority access request', async () => {
  await go(page, 'Account');
  await field(page, 'Full name').locator('input').fill('Ravi Kumar');
  await field(page, 'Organisation').locator('input').fill('Bengaluru Traffic Police');
  await field(page, 'Designation').locator('input').fill('Inspector');
  await field(page, 'Official email').locator('input').fill('ravi.kumar@example.gov.in');
  await field(page, 'Reason').locator('textarea').fill('I manage signal operations for the east division and review incidents.');
  await page.getByRole('button', { name: /Send request/ }).click();
  await expect(page.locator('table tbody tr').first()).toContainText('pending');
  await expect(page.locator('.page-heading')).toContainText(`Signed in as ${EMAIL}`);
});

test('the password is changed on the account page', async () => {
  await field(page, 'Current password').locator('input').fill('citizen-password-1');
  await field(page, 'New password').locator('input').fill('citizen-password-2');
  await field(page, 'Repeat the new password').locator('input').fill('citizen-password-3');
  await page.getByRole('button', { name: /Change password/ }).click();
  await expect(page.locator('.form-card', { hasText: 'Change password' }).locator('.inline-error')).toHaveText('The two new passwords do not match.');
  await field(page, 'Repeat the new password').locator('input').fill('citizen-password-2');
  await page.getByRole('button', { name: /Change password/ }).click();
  await expect(toastText(page)).toHaveText(/Password changed/);
  await go(page, 'Notifications');
  await expect(page.locator('.page-heading')).toContainText('Trip alerts');   // this session keeps working
});

test('a session from before the password change is sent back to sign in', async ({ browser }) => {
  const other = await stubbedPage(browser, {}, errors);
  await other.context().addInitScript(token => sessionStorage.setItem('flowstateToken', token), citizen);
  await other.goto('/user', { waitUntil: 'domcontentloaded' });
  await expect(other.locator('.form-notice')).toContainText("password was changed. Sign in again.");
  await other.context().close();
});

test('after signing out, the commuter signs back in with their email', async () => {
  await page.locator('.topbar .account-button').click();
  await page.getByRole('button', { name: /Continue as a commuter/ }).click();
  await expect(page.getByText('Welcome back')).toBeVisible();
  await page.getByPlaceholder('you@example.com').fill(EMAIL.toUpperCase());
  await page.getByPlaceholder('Your password').fill('citizen-password-1');   // the old password
  await page.getByRole('button', { name: /Sign in securely/ }).click();
  await expect(page.locator('.inline-error')).toHaveText('Email or password is incorrect.');
  await page.getByPlaceholder('Your password').fill('citizen-password-2');
  await page.getByRole('button', { name: /Sign in securely/ }).click();
  await expect(page.getByText('Where are you headed?')).toBeVisible();
  citizen = await page.evaluate(() => sessionStorage.getItem('flowstateToken'));
});

test('a rejected Google key shows a clear map notice', async () => {
  await page.evaluate(() => window.gm_authFailure());
  await expect(page.locator('.google-map-setup-note').first()).toContainText('rejected the browser key');
});

test('commuter pages fit a 390 px phone screen', async ({ browser }) => {
  const phone = await stubbedPage(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, errors);
  await phone.context().addInitScript(token => sessionStorage.setItem('flowstateToken', token), citizen);
  await phone.goto('/user', { waitUntil: 'domcontentloaded' });
  await expect(phone.getByText('Where are you headed?')).toBeVisible();
  const wide = [];
  if (await overflowAt(phone)) wide.push(`home (${await overflowAt(phone)})`);
  for (const name of ['Road forecast', 'Plan a trip', 'Start a journey', 'Report an incident', 'Notifications', 'Account']) {
    await phone.getByRole('button', { name: 'Open menu' }).click();
    await go(phone, name);
    await phone.waitForTimeout(400);
    if (await overflowAt(phone)) wide.push(`${name} (${await overflowAt(phone)})`);
  }
  await phone.context().close();
  expect(wide).toEqual([]);
});

test('no script errors on any commuter page', async () => {
  expect(errors).toEqual([]);
});
