// Browser tests. From tests/browser: npm install, npx playwright install chromium, npx playwright test
// The config starts tests/browser/server.py (fake data, fake Google services) with the project's .venv.
const path = require('path');
const { defineConfig } = require('@playwright/test');

const root = path.resolve(__dirname, '..', '..');
const python = process.platform === 'win32'
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python');
const PORT = 8765;
// A second server with no Google keys, for the OpenStreetMap map (3-openstreetmap.spec.js).
const OSM_PORT = 8766;
const server = (port, mode) => ({
  command: `"${python}" "${path.join(__dirname, 'server.py')}" ${port} ${mode}`,
  url: `http://127.0.0.1:${port}/api/health`,
  timeout: 300_000,
  reuseExistingServer: false,
  stdout: 'ignore',
  stderr: 'pipe',
});

module.exports = defineConfig({
  testDir: __dirname,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1360, height: 900 }, trace: 'retain-on-failure' },
  webServer: [server(PORT, 'google'), server(OSM_PORT, 'openstreetmap')],
});
