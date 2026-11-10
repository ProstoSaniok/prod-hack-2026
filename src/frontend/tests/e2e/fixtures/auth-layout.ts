import { expect, Page, Route } from '@playwright/test';
import type { DevelopmentPolicy, UserProfile, Wallet } from '../../../src/app/api/backend.service';
import type { Project, PublishedCase, RemoteFile } from '../../../src/app/api/learning.service';

export const fixturePassword = 'Practice2026!';
export const fixtureEmail = 'learner@example.com';
export const newFixtureEmail = 'new.learner@example.com';
export const projectId = '10000000-0000-4000-8000-000000000001';
export const caseId = '20000000-0000-4000-8000-000000000001';

interface Account {
  id: string;
  email: string;
  balance: number;
  organizer: boolean;
}

/** All HTTP traffic stays in the fixture; file writes persist only in this test. */
export async function authLayoutFixture(page: Page) {
  const accounts = new Map<string, Account>([
    [fixtureEmail, { id: 'learner', email: fixtureEmail, balance: 37, organizer: false }],
    [
      'organizer.local@example.com',
      { id: 'organizer', email: 'organizer.local@example.com', balance: 0, organizer: true },
    ],
  ]);
  const tokens = new Map<string, Account>();
  const events: string[] = [];
  const unexpected: string[] = [];
  let sequence = 0;
  const study: PublishedCase = {
    id: caseId,
    title: 'Поиск по каталогу',
    description: 'Исправьте поиск и фильтр категории.',
    expected_result: 'Корректный результат поиска.',
    difficulty: 'review_minus',
    content_version: 1,
    competencies: [],
  };
  const adminCase = {
    ...study,
    status: 'draft',
    frozen: false,
    version: 1,
    created_at: '2026-10-17T10:00:00Z',
  };
  const project: Project = {
    id: projectId,
    case_id: caseId,
    case_version: 1,
    status: 'active',
    head_commit_sha: 'head-1',
  };
  const file: RemoteFile = {
    path: 'solution.md',
    sha: 'file-1',
    commit_sha: 'head-1',
    content: 'Начальное решение',
  };
  const policy: DevelopmentPolicy = {
    version: 'prodguild-development-v1',
    progression_version: 'p02-v1',
    state: 'configured',
    formula: 'score * weight',
    success_score: 70,
    quality_score: 70,
    probation_required_reviews: 3,
    display_cap: 100,
    priority_cost: 5,
    grade_gates: [
      { grade: 'performer', minimum_value: 0 },
      { grade: 'review_minus', minimum_value: 25 },
      { grade: 'review_plus', minimum_value: 50 },
      { grade: 'expert', minimum_value: 100 },
    ].map(gate => ({
      ...gate,
      successful_cases: 0,
      confirmed_good_reviews: 0,
      previous_probation_required: false,
      independent_readiness_required: gate.grade === 'expert',
    })) as DevelopmentPolicy['grade_gates'],
  };
  const accountFor = (route: Route): Account | undefined =>
    tokens.get(
      route
        .request()
        .headers()
        ['authorization']?.replace(/^Bearer /, '') ?? '',
    );
  const balance = (account: Account): Wallet => ({
    settled: account.balance,
    available: account.balance,
    reserved: 0,
    unit: 'credit',
  });
  await page.route('**/auth/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const account = accountFor(route);
    if (path === '/auth/sign_in/email' || path === '/auth/sign_up/email') {
      const body = route.request().postDataJSON() as { email: string; password: string };
      expect(body.password.length).toBeGreaterThanOrEqual(8);
      if (path === '/auth/sign_up/email')
        accounts.set(body.email, {
          id: `account-${accounts.size}`,
          email: body.email,
          balance: 10,
          organizer: false,
        });
      const signingIn = accounts.get(body.email);
      if (!signingIn) return route.fulfill({ status: 401, json: { code: 'INVALID_CREDENTIALS' } });
      const token = `layout-session-${++sequence}`;
      tokens.set(token, signingIn);
      events.push(`sign-in:${body.email}`);
      return route.fulfill({ json: { access_token: token, token_type: 'bearer' } });
    }
    if (!account) return route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } });
    if (path === '/auth/me')
      return route.fulfill({
        json: {
          id: account.id,
          role: 'user',
          capabilities: [account.organizer ? 'organizer' : 'participant'],
        },
      });
    if (path === '/auth/sign_out') {
      tokens.delete(
        route
          .request()
          .headers()
          ['authorization'].replace(/^Bearer /, ''),
      );
      events.push('sign-out');
      return route.fulfill({ status: 204 });
    }
    unexpected.push(path);
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url()),
      path = url.pathname,
      method = route.request().method();
    const account = accountFor(route);
    if (!account) return route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } });
    if (path === '/api/v1/me/review-mode')
      return route.fulfill({
        json: {
          can_review: false,
          can_assess_readiness: false,
          enabled: false,
          status: 'disabled',
        },
      });
    if (path === '/api/v1/me/wallet') return route.fulfill({ json: balance(account) });
    if (path === '/api/v1/me/profile') {
      const profile: UserProfile = {
        id: account.id,
        display_name: 'Участник',
        grade: 'performer',
        competencies: [],
        wallet: balance(account),
        karma: 0,
        prodits: account.balance,
      };
      return route.fulfill({ json: profile });
    }
    if (path === '/api/v1/development/policy') return route.fulfill({ json: policy });
    if (
      [
        '/api/v1/me/wallet/transactions',
        '/api/v1/me/skills',
        '/api/v1/me/history',
        '/api/v1/competencies',
        '/api/v1/me/submissions',
      ].includes(path)
    )
      return route.fulfill({ json: [] });
    if (path === '/api/v1/cases') return route.fulfill({ json: [study] });
    if (path === `/api/v1/cases/${caseId}`) return route.fulfill({ json: study });
    if (path === '/api/v1/me/projects') return route.fulfill({ json: [project] });
    if (path === `/api/v1/projects/${projectId}`) return route.fulfill({ json: project });
    if (path === `/api/v1/projects/${projectId}/files`) return route.fulfill({ json: [file] });
    if (path === `/api/v1/projects/${projectId}/files/content`) {
      expect(url.searchParams.get('path')).toBe(file.path);
      if (method === 'PUT') {
        const body = route.request().postDataJSON() as { content: string; sha: string };
        expect(body.sha).toBe(file.sha);
        expect(route.request().headers()['idempotency-key']).toBeTruthy();
        file.content = body.content;
        file.sha = `file-${++sequence}`;
        file.commit_sha = project.head_commit_sha = `head-${sequence}`;
        events.push('file-saved');
      }
      return route.fulfill({ json: file });
    }
    if (account.organizer && path === '/api/v1/admin/cases')
      return route.fulfill({ json: [adminCase] });
    if (account.organizer && path === `/api/v1/admin/cases/${caseId}`)
      return route.fulfill({ json: adminCase });
    if (
      account.organizer &&
      [
        `/api/v1/admin/cases/${caseId}/files`,
        `/api/v1/admin/cases/${caseId}/reference-solution/files`,
      ].includes(path)
    )
      return route.fulfill({ json: [] });
    if (account.organizer && path.endsWith('/reference-solution'))
      return route.fulfill({ json: { version: 1, head_commit_sha: 'reference-1', ready: false } });
    unexpected.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
  return { accounts, events, unexpected, file, project, study };
}

export async function signInFixture(page: Page, email = fixtureEmail): Promise<void> {
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(fixturePassword);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
}

export async function openFixtureProject(page: Page): Promise<void> {
  await page.goto('/login');
  await signInFixture(page);
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto(`/projects/${projectId}`);
  await expect(
    page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true }),
  ).toBeVisible();
}
