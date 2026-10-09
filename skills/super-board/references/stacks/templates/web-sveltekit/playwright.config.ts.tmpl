import { defineConfig } from '@playwright/test';

// Browser tests live in e2e/*.e2e.ts so Vitest (which reads *.test.ts and
// *.spec.ts) never picks them up. They run against the production build.
export default defineConfig({
	testDir: 'e2e',
	testMatch: '**/*.e2e.ts',
	webServer: {
		command: 'pnpm run build && pnpm run preview --port 4173',
		port: 4173,
		reuseExistingServer: !process.env.CI
	},
	use: { baseURL: 'http://localhost:4173' }
});
