import type { PlaywrightTestConfig } from '@playwright/test';

// New suites need an explicit classification before they can run.
const suiteFiles = {
  fixtures: [
    'admin-autosave.spec.ts',
    'admin-conflicts.spec.ts',
    'admin-workspace.spec.ts',
    'admin.spec.ts',
    'brand-theme.spec.ts',
    'catalog-results-simplification.spec.ts',
    'catalog-skill-filter.spec.ts',
    'credits-auth-layout.spec.ts',
    'criteria.spec.ts',
    'critical-workflows.spec.ts',
    'inline-file-creation.spec.ts',
    'product-sidebar.spec.ts',
    'profile-achievements.spec.ts',
    'project-autosave.spec.ts',
    'review-access-validation.spec.ts',
    'review-guidance.spec.ts',
    'review-navigation.spec.ts',
    'review-workspace.spec.ts',
    'sidebar.spec.ts',
    'site-header-mobile.spec.ts',
    'submission-master.spec.ts',
    'ui-kit.spec.ts',
    'ui-ux-polish.spec.ts',
    'workspace-layout.spec.ts',
    'workspace-maximize.spec.ts',
  ],
  live: [
    'api-integration.spec.ts',
    'case-api-integration.spec.ts',
    'catalog-api-integration.spec.ts',
    'default-role-entry.spec.ts',
    'editor-recovery-api.spec.ts',
    'learning-api-integration.spec.ts',
    'organizer-access.spec.ts',
    'review-api-integration.spec.ts',
    'review-quality-api-integration.spec.ts',
    'submission-api-integration.spec.ts',
  ],
  historical: [
    // These fixtures describe attachment endpoints absent from the fixed master backend.
    'attachments.spec.ts',
    'expert.spec.ts',
    'local-demo-frontend.spec.ts',
    'local-demo.spec.ts',
    'reviews.spec.ts',
    'solution-splitter.spec.ts',
    'user-case-storage.spec.ts',
    'user-cases.spec.ts',
    'user-solutions.spec.ts',
  ],
};

const suite = process.env['PRODGUILD_E2E_SUITE'] ?? 'fixtures';
if (suite !== 'fixtures' && suite !== 'live' && suite !== 'historical') {
  throw new Error('PRODGUILD_E2E_SUITE must be fixtures, live or historical.');
}

const configuredBaseURL = process.env['BASE_URL']?.trim();
// Historical tests also contain real sign-ins and registrations.
const requiresIsolatedBackend = suite !== 'fixtures';
if (
  requiresIsolatedBackend &&
  (!configuredBaseURL || process.env['PRODGUILD_E2E_ISOLATED_BACKEND'] !== '1')
) {
  throw new Error(
    `${suite} tests require BASE_URL and PRODGUILD_E2E_ISOLATED_BACKEND=1. ` +
      'Use a dedicated test backend: these tests change accounts and business data.',
  );
}
if (configuredBaseURL && !/^https?:\/\//.test(configuredBaseURL)) {
  throw new Error('BASE_URL must be an absolute http:// or https:// frontend URL.');
}
const baseURL = configuredBaseURL || 'http://127.0.0.1:4200';

export default {
  testDir: './tests/e2e',
  testMatch: suiteFiles[suite].map(file => `**/${file}`),
  fullyParallel: !requiresIsolatedBackend,
  workers: requiresIsolatedBackend ? 1 : 4,
  outputDir: `test-results/${suite}`,
  forbidOnly: !!process.env['CI'],
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: `playwright-report/${suite}` }]],
  use: {
    baseURL,
    channel: process.env['BROWSER_CHANNEL'],
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'mobile', use: { viewport: { width: 375, height: 812 } } },
    { name: 'tablet', use: { viewport: { width: 768, height: 1024 } } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'wide', use: { viewport: { width: 2560, height: 1440 } } },
  ],
  webServer:
    configuredBaseURL || requiresIsolatedBackend
      ? undefined
      : {
          command: 'npm start -- --port 4200 --live-reload=false --hmr=false',
          env: { PRODGUILD_BACKEND_URL: 'http://127.0.0.1:9' },
          url: baseURL,
          reuseExistingServer: false,
          timeout: 120_000,
        },
} satisfies PlaywrightTestConfig;
