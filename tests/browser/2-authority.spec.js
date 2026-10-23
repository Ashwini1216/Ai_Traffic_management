// Authority console, as one signed-in session. The browser clock is set to 01:00 IST tomorrow,
// when the UTC date is still the previous day, to check that dates follow Bengaluru time.
const { test, expect } = require('@playwright/test');
const { OFFICER, api, officerToken, newCitizen, stubbedPage, go, field, toastText, overflowAt } = require('./helpers');

const errors = [];
const istTomorrow = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date(Date.now() + 86_400_000));
let page;
let officer;
let reporter;
let applicant;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ browser }) => {
  officer = await officerToken();
  reporter = await newCitizen();
  applicant = await newCitizen();
  const point = { latitude: 12.9716, longitude: 77.6412 };
  await api('POST', '/api/incidents', { category: 'accident', description: 'Two cars collided at the signal', ...point }, reporter.token);
  await api('POST', '/api/incidents', { category: 'accident', description: 'Same collision, one lane blocked', ...point }, reporter.token);
  const segments = (await api('GET', '/api/segments', null, officer)).body.segments;
  const at = road => segments.find(s => s.road === road);
  const [a, b] = [at('Hebbal Flyover'), at('Ballari Road')];
  await api('POST', '/api/trips', { origin: 'Hebbal Flyover', destination: 'Ballari Road', origin_lat: a.latitude, origin_lon: a.longitude,
    destination_lat: b.latitude, destination_lon: b.longitude, departure_at: new Date(Date.now() + 2 * 3_600_000).toISOString() }, reporter.token);
  await api('POST', '/api/access-requests', { full_name: 'Ravi Kumar', organization: 'Bengaluru Traffic Police', designation: 'Inspector',
    official_email: 'ravi.kumar@example.gov.in', reason: 'I manage signal operations for the east division.' }, applicant.token);
  await api('POST', '/api/cameras', { camera_id: 'cam-geo-01', display_name: 'Geo camera', area: 'Indiranagar', road: '100 Feet Road',
    latitude: 12.9719, longitude: 77.6412, source_type: 'authorized_cctv', authorization_status: 'approved', operational_status: 'online' }, officer);

  page = await stubbedPage(browser, { timezoneId: 'Asia/Kolkata' }, errors);
  await page.clock.setFixedTime(new Date(`${istTomorrow}T01:00:00+05:30`));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: /Authority sign in/ }).click();
  await page.getByPlaceholder('Authority email or username').fill(OFFICER.username);
  await page.getByPlaceholder('Your password').fill(OFFICER.password);
  await page.getByRole('button', { name: /Sign in securely/ }).click();
  await expect(page.getByText('City operations')).toBeVisible();
});

test.afterAll(async () => { await page.context().close(); });

test('navigation is grouped and the overview shows its metrics', async () => {
  await expect(page.locator('.sidebar .nav-label')).toHaveText(['OPERATIONS', 'DATA AND MODELS', 'ADMINISTRATION']);
  await expect(page.locator('.metric-card')).toHaveCount(4);
});

test('the zone monitor lists and draws every zone', async () => {
  await go(page, 'Zone monitor');
  await expect(page.locator('.zone-item').first()).toBeVisible();
  expect(await page.locator('.zone-item').count()).toBeGreaterThanOrEqual(8);
  expect((await page.evaluate(() => window.__live())).circles).toBeGreaterThanOrEqual(8);
});

test('a report is verified, then resolved, from the incident map', async () => {
  await go(page, 'Incident map');
  const card = page.locator('.authority-report', { hasText: 'Two cars collided at the signal' });
  await expect(card).toContainText('1 confirmation');
  await card.getByRole('button', { name: /Verify/ }).click();
  await expect(toastText(page)).toHaveText(/marked verified/);
  await page.locator('select.compact-select').selectOption('verified');
  await page.locator('.authority-report', { hasText: 'Two cars collided at the signal' }).getByRole('button', { name: /Resolve/ }).click();
  await expect(toastText(page)).toHaveText(/marked resolved/);
});

test('an official notice is published from a known road, then ended', async () => {
  await go(page, 'Official notices');
  await field(page, 'Description').locator('textarea').fill('VIP convoy between 10:00 and 11:00, expect stoppages');
  await field(page, 'Location from a known road').locator('select').selectOption({ label: 'Hebbal Flyover, Hebbal' });
  await page.getByRole('button', { name: /Publish notice/ }).click();
  await expect(toastText(page)).toHaveText(/Notice published until/);
  const notice = page.locator('.report-item', { hasText: 'VIP convoy' });
  await expect(notice).toContainText('in force');
  await notice.getByRole('button', { name: /End notice/ }).click();
  await expect(toastText(page)).toHaveText(/Notice ended/);
});

test('checking zones raises a closure alert with a recommendation', async () => {
  const notice = await api('POST', '/api/incidents/official', { category: 'road_closure', description: 'Trinity Circle closed',
    area: 'M.G. Road', road: 'Trinity Circle', latitude: 12.9727, longitude: 77.6196, closure: true }, officer);
  await go(page, 'Alerts');
  await page.getByRole('button', { name: /Check zones now/ }).click();
  await expect(toastText(page)).toHaveText(/Checked zones/);
  const alert = page.locator('.ops-alert', { hasText: 'Road closure' }).first();
  await expect(alert).toContainText('Recommended:');
  await alert.getByRole('button', { name: /Acknowledge/ }).click();
  await expect(toastText(page)).toHaveText(/acknowledged/);
  await api('PATCH', `/api/incidents/${notice.body.incident_id}`, { status: 'resolved' }, officer);
});

test('planned demand is shown without naming anyone', async () => {
  await go(page, 'Planned demand');
  const tile = page.locator('.stat-tile', { hasText: 'Planned passages' });
  await expect(tile).toBeVisible();
  expect(Number(await tile.locator('.stat-value').innerText())).toBeGreaterThanOrEqual(1);
  await expect(page.locator('.page-wrap')).not.toContainText(reporter.name);
});

test('the coverage registry lists every road and saves an edit', async () => {
  await go(page, 'Data coverage');
  await expect(page.locator('.segment-card tbody tr')).toHaveCount(16);
  await page.locator('.segment-card tbody tr').first().getByRole('button', { name: 'Edit' }).click();
  await page.getByRole('button', { name: /^Save$/ }).click();
  await expect(toastText(page)).toHaveText(/Road updated/);
});

test('observations are stored and verified, and jobs and quarantine are shown', async () => {
  await go(page, 'Observations & jobs');
  await field(page, 'Road').locator('select').selectOption({ label: '100 Feet Road, Indiranagar' });
  await field(page, 'Date').locator('input').fill('2026-10-01');
  await field(page, 'Traffic volume').locator('input').fill('1200');
  await field(page, 'Average speed').locator('input').fill('30');
  await field(page, 'Travel time index').locator('input').fill('1.5');
  await page.getByRole('button', { name: /Store observation/ }).click();
  await expect(toastText(page)).toHaveText(/stored as unverified/);
  await page.locator('tr', { hasText: '2026-10-01 Morning' }).getByRole('button', { name: 'Verify' }).click();
  await expect(toastText(page)).toHaveText(/Observation verified/);
  await expect(page.locator('.report-list-card', { hasText: 'Background jobs' }).locator('tbody tr')).toHaveCount(8);
  await page.locator('tr', { hasText: 'Zone alerts' }).getByRole('button', { name: /Run now/ }).click();
  await expect(toastText(page)).toHaveText(/Zone alerts: ok/);
  expect(await page.locator('.report-list-card', { hasText: 'Quarantine' }).locator('tbody tr').count()).toBeGreaterThanOrEqual(1);
});

test('the models page shows the version, its checks, and a replay', async () => {
  const segments = (await api('GET', '/api/segments', null, officer)).body.segments;
  const road = segments.find(s => s.road === '100 Feet Road');
  let replayDate = null;
  for (let day = 10; day < 28 && !replayDate; day += 1) {
    const date = `2022-03-${String(day).padStart(2, '0')}`;
    const replay = await api('POST', '/api/forecast/replay', { segment_id: road.segment_id, as_of_date: date, as_of_time_block: 'Noon', horizons: 3 }, officer);
    if (replay.status === 200) replayDate = date;
  }
  expect(replayDate).not.toBeNull();
  await go(page, 'Models');
  await expect(page.locator('.check-list li').first()).toBeVisible();
  await expect(page.locator('.check-list')).toContainText('Random Forest without LSTM');
  await expect(page.locator('.bar-label', { hasText: 'Hybrid model' })).toHaveCount(1);
  const version = page.locator('tbody tr').first();
  if (/rejected/.test(await version.innerText())) await expect(version.getByRole('button')).toHaveCount(0);
  await field(page, 'Road').locator('select').selectOption({ label: '100 Feet Road, Indiranagar' });
  await field(page, 'As of date').locator('input').fill(replayDate);
  await page.getByRole('button', { name: /Replay/ }).click();
  await expect(page.locator('.form-card', { hasText: 'Replay on past records' }).locator('tbody tr')).toHaveCount(3);
});

test('an access request is approved, then the new authority is revoked', async () => {
  await go(page, 'Access requests');
  await page.locator('tr', { hasText: applicant.name }).getByRole('button', { name: 'Approve' }).click();
  await expect(toastText(page)).toHaveText(/approved/);
  const account = page.locator('.report-list-card', { hasText: 'Authority accounts' }).locator('tr', { hasText: applicant.name });
  await expect(account).toBeVisible();
  await account.getByRole('button', { name: 'Revoke' }).click();
  await expect(toastText(page)).toHaveText(/revoked/);
  await expect(account).toHaveCount(0);
});

test('camera settings are saved, and editing keeps coordinates and the stream', async () => {
  await go(page, 'CCTV review');
  await field(page, 'Camera ID').locator('input').fill('cam-ui-01');
  await field(page, 'Display name').locator('input').fill('Silk Board east');
  await field(page, 'Area').locator('input').fill('Electronic City');
  await field(page, 'Road').locator('input').fill('Silk Board Junction');
  await field(page, 'Authorization').locator('select').selectOption('approved');
  await field(page, 'Operational status').locator('select').selectOption('online');
  await field(page, 'Stream address').locator('input').fill('rtsp://camera.example/stream');
  await field(page, 'Counting line').locator('input').fill('0.6');
  const save = name => Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/cameras') && r.request().method() === 'POST' && r.ok()),
    page.getByRole('button', { name }).click(),
  ]);
  await save(/Save camera source/);
  await expect(page.locator('tr', { hasText: 'Silk Board east' })).toContainText('every 5 min · counting line');
  await field(page, 'Update an existing camera').locator('select').selectOption('cam-ui-01');
  await save(/Save camera changes/);
  await field(page, 'Update an existing camera').locator('select').selectOption('cam-geo-01');
  await save(/Save camera changes/);
  const cameras = (await api('GET', '/api/cameras', null, officer)).body.cameras;
  const geo = cameras.find(c => c.camera_id === 'cam-geo-01');
  expect([geo.latitude, geo.longitude]).toEqual([12.9719, 77.6412]);
  expect(cameras.find(c => c.camera_id === 'cam-ui-01').has_stream).toBe(true);
});

test('the clip date defaults to the Bengaluru date', async () => {
  await expect(page.locator('.form-card', { hasText: 'Review a video clip' }).locator('input[type="date"]')).toHaveValue(istTomorrow);
});

test('the traffic data overview matches the API', async () => {
  await go(page, 'Traffic data');
  const summary = (await api('GET', '/api/summary', null, officer)).body;
  const value = await page.locator('.metric-card', { hasText: 'Historical observations' }).locator('.metric-value').innerText();
  expect(Number(value.replace(/,/g, ''))).toBe(summary.record_count);
  await expect(page.locator('.traffic-card .meter-row')).toHaveCount(3);
  await expect(page.locator('.model-card')).toContainText('acceptance checks');
});

test('place search shows the address as text, never as HTML', async () => {
  await go(page, 'Place search');
  await page.getByPlaceholder(/Search an address or landmark/).fill('Indiranagar');
  await page.getByRole('button', { name: /^Search$/ }).click();
  await expect(page.locator('.selected-place')).toBeVisible();
  const state = await page.evaluate(() => ({ injected: window.__xss === 1, kinds: window.__info }));
  expect(state.injected).toBe(false);
  expect(state.kinds).not.toContain('html-string');
});

test('authority pages fit a 390 px phone screen', async ({ browser }) => {
  const phone = await stubbedPage(browser, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }, errors);
  await phone.context().addInitScript(token => sessionStorage.setItem('flowstateToken', token), officer);
  await phone.goto('/authority', { waitUntil: 'domcontentloaded' });
  await expect(phone.getByText('City operations')).toBeVisible();
  const wide = [];
  for (const name of ['Zone monitor', 'Incident map', 'Official notices', 'Alerts', 'Planned demand', 'Data coverage',
    'Observations & jobs', 'Models', 'CCTV review', 'Traffic data', 'Access requests']) {
    await phone.getByRole('button', { name: 'Open menu' }).click();
    await go(phone, name);
    await phone.waitForTimeout(500);
    if (await overflowAt(phone)) wide.push(`${name} (${await overflowAt(phone)})`);
  }
  await phone.context().close();
  expect(wide).toEqual([]);
});

test('no page shows [object Object] and no script errors occurred', async () => {
  await expect(page.locator('body')).not.toContainText('[object Object]');
  expect(errors).toEqual([]);
});
