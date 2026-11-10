import { expect, Page, test } from '@playwright/test';
import { emptyAttachments } from './fixtures/attachments';

const study = {
  id: 'case-one',
  title: 'Поиск по каталогу',
  description: 'Найдите и исправьте ошибку в поиске.',
  expected_result: 'Работающий поиск',
  difficulty: 'review_plus',
  content_version: 1,
  competencies: [],
};
const wallet = { settled: 10, reserved: 0, available: 10, unit: 'credit' };
const assignment = {
  id: 'catalog-search',
  kind: 'review',
  review_id: null,
  revision_id: null,
  submission_id: 'submission-one',
  status: 'assigned',
  lease_expires_at: '2030-10-17T10:00:00Z',
};
const profile = {
  id: 'ui-user',
  display_name: 'Участник',
  grade: 'performer',
  competencies: [],
  wallet,
  karma: 0,
  prodits: 10,
};
const policy = {
  version: 'prodguild-development-v1',
  progression_version: 'p02-v1',
  state: 'configured',
  formula: 'score * weight * (1 + 0.2 * E / 100)',
  success_score: 70,
  quality_score: 70,
  probation_required_reviews: 3,
  display_cap: 100,
  grade_gates: ['performer', 'review_minus', 'review_plus', 'expert'].map((grade, index) => ({
    grade,
    minimum_value: [0, 25, 50, 75][index],
    successful_cases: index ? 2 : 0,
    confirmed_good_reviews: index > 1 ? 3 : 0,
    previous_probation_required: index > 1,
    independent_readiness_required: index === 3,
  })),
};

async function fixture(page: Page): Promise<void> {
  await page.addInitScript(() => {
    if (sessionStorage.getItem('prodguild.api.session.v1')) return;
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'ui-test', email: 'performer.local@example.com', name: 'Участник' }),
    );
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'ui-user', role: 'user', capabilities: [] } }),
  );
  await page.route('**/auth/sign_in/email', route =>
    route.fulfill({ json: { access_token: 'admin-test', token_type: 'bearer' } }),
  );
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('attachments')) return route.fulfill({ json: emptyAttachments() });
    if (url.pathname.endsWith('/me/review-mode'))
      return route.fulfill({ json: { enabled: false, status: 'disabled', can_review: false } });
    if (url.pathname.endsWith('/me/wallet')) return route.fulfill({ json: wallet });
    if (url.pathname.endsWith('/me/profile')) return route.fulfill({ json: profile });
    if (url.pathname.endsWith('/development/policy')) return route.fulfill({ json: policy });
    if (url.pathname === '/api/v1/me/reviews')
      return route.fulfill({ json: { items: [], next_offset: null } });
    if (url.pathname === '/api/v1/review-assignments/catalog-search')
      return route.fulfill({ json: assignment });
    if (url.pathname === '/api/v1/review-assignments/catalog-search/workspace')
      return route.fulfill({
        json: {
          assignment,
          case: study,
          draft: {
            version: 0,
            score: null,
            verdict: null,
            feedback: { strengths: '', problems: '', next_steps: '' },
            comments: [],
          },
        },
      });
    if (/\/review-assignments\/catalog-search\/(files|diff)$/.test(url.pathname))
      return route.fulfill({ json: { entries: [], next_offset: null } });
    if (url.pathname.endsWith('/wallet/transactions'))
      return route.fulfill({
        json: [
          {
            id: 'tx',
            sequence: 1,
            kind: 'welcome',
            settled_delta: 10,
            reserved_delta: 0,
            created_at: '2026-10-17T10:00:00Z',
          },
        ],
      });
    if (url.pathname === '/api/v1/cases')
      return route.fulfill({
        json: [study, { ...study, id: 'case-two', title: 'Платёжная форма' }].filter(item =>
          item.title
            .toLocaleLowerCase('ru')
            .replaceAll('ё', 'е')
            .includes(url.searchParams.get('q') ?? ''),
        ),
      });
    if (url.pathname === '/api/v1/cases/case-one') return route.fulfill({ json: study });
    if (url.pathname === '/api/v1/projects/project-one')
      return route.fulfill({
        json: {
          id: 'project-one',
          case_id: study.id,
          case_version: 1,
          status: 'active',
          head_commit_sha: 'head',
        },
      });
    if (url.pathname === '/api/v1/admin/cases') return route.fulfill({ json: [] });
    return route.fulfill({ json: [] });
  });
}

async function checkLayout(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await expect(page.locator('h1')).toHaveCSS('font-family', /Tinkoff Sans/);
}

test.beforeEach(async ({ page }) => fixture(page));

test('search debounce, immediate clear, reward and typography', async ({ page }, info) => {
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows).toHaveCount(2);
  await expect(rows.first().locator('.reward .currency-number')).toHaveText('+3');
  await expect(rows.first().locator('.reward img')).toBeVisible();
  await page.clock.install();
  const search = page.getByRole('searchbox');
  await search.click();
  await search.pressSequentially('  ПЛАТЕЖ  ');
  await expect(rows).toHaveCount(2);
  await page.clock.fastForward(301);
  await expect(rows).toHaveCount(1);
  await search.fill('несуществующий');
  await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await expect(rows).toHaveCount(2);
  await page.clock.fastForward(301);
  await expect(rows).toHaveCount(2);
  await search.fill('несуществующий');
  await page.clock.fastForward(301);
  await expect(page.getByText('Ничего не найдено.', { exact: false })).toBeVisible();
  await expect(page.getByText('Опубликованных кейсов пока нет.')).toHaveCount(0);
  await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('catalog.png'), fullPage: true });
  await rows.first().click();
  await expect(page).toHaveURL(/\/cases\/case-one$/);
  await expect(page.getByRole('heading', { name: study.title, exact: true })).toBeVisible();
  await expect(page.locator('.reward .currency-number')).toHaveText('+3');
  await expect(page.locator('.reward img')).toBeVisible();
  await expect(page.locator('.reward')).toContainText('за первое успешное решение');
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('case.png'), fullPage: true });
});

test('loading does not look empty, failure can be retried', async ({ page }) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/api/v1/cases?*', async route => {
    await pending;
    await route.fulfill({ status: 503, json: {} });
  });
  await page.goto('/catalog');
  await expect(page.getByRole('status').filter({ hasText: 'Загружаем кейсы' })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Загружаем кейсы…' })).toBeVisible();
  await expect(page.getByText('Опубликованных кейсов пока нет.')).toHaveCount(0);
  release();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.route('**/api/v1/cases?*', route => route.fulfill({ json: [study] }));
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(page.getByRole('heading', { name: study.title })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('failed next page preserves current catalogue and retries the same offset', async ({
  page,
}) => {
  const firstPage = Array.from({ length: 21 }, (_, index) => ({
    ...study,
    id: `first-${index}`,
    title: `Кейс первой страницы ${index + 1}`,
  }));
  let attempts = 0;
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/api/v1/cases?*', async route => {
    const offset = new URL(route.request().url()).searchParams.get('offset');
    if (offset === '20') {
      attempts++;
      if (attempts === 1) {
        await pending;
        await route.fulfill({ status: 503, json: {} });
      } else await route.fulfill({ json: [study] });
    } else await route.fulfill({ json: firstPage });
  });
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  const next = page.getByRole('button', { name: 'Далее', exact: true });
  await expect(rows).toHaveCount(20);
  await next.click();
  await expect(next).toBeDisabled();
  await expect(rows).toHaveCount(20);
  await expect(page.getByText('Страница 1', { exact: true })).toBeVisible();
  release();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(rows).toHaveCount(20);
  await expect(page.getByText('Страница 1', { exact: true })).toBeVisible();
  await next.click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByText('Страница 2', { exact: true })).toBeVisible();
  await expect(rows.first()).toContainText(study.title);
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(attempts).toBe(2);
});

test('profile partial failure, independent retry and no diagnostics', async ({ page }, info) => {
  const diagnostics: string[] = [];
  page.on('request', request => {
    if (/\/(healthcheck|ready|metrics)(\?|$)/.test(request.url())) diagnostics.push(request.url());
  });
  await page.route('**/api/v1/me/wallet/transactions?*', route =>
    route.fulfill({ status: 503, json: {} }),
  );
  await page.goto('/profile');
  const balance = page.getByRole('region', { name: 'Баланс', exact: true });
  const history = page.getByRole('region', { name: 'История операций', exact: true });
  await expect(balance.locator('.currency-number')).toHaveText('10');
  await expect(balance.locator('img')).toBeVisible();
  await expect(history.getByRole('alert')).toBeVisible();
  await expect(history.getByText('Операций пока нет.')).toHaveCount(0);
  await page.route('**/api/v1/me/wallet/transactions?*', route => route.fulfill({ json: [] }));
  await history.getByRole('button', { name: 'Повторить загрузку истории' }).click();
  await expect(history.getByText('Операций пока нет.')).toBeVisible();
  await page.route('**/api/v1/me/profile', route => route.fulfill({ status: 503, json: {} }));
  await page.waitForTimeout(1100);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('.profile-page > [role="alert"]')).toBeVisible();
  await expect(balance.locator('.currency-number')).toHaveText('10');
  await expect(balance.locator('img')).toBeVisible();
  await expect(history.getByRole('alert')).toHaveCount(0);
  await page.route('**/api/v1/me/profile', route => route.fulfill({ json: profile }));
  await page.getByRole('button', { name: 'Повторить загрузку профиля' }).click();
  await expect(page.locator('.profile-page > [role="alert"]')).toHaveCount(0);
  await expect(balance.locator('.currency-number')).toHaveText('10');
  await expect(balance.locator('img')).toBeVisible();
  await expect(page.getByText('Идентификатор', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Каталог компетенций', { exact: true })).toHaveCount(0);
  expect(diagnostics).toEqual([]);
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('profile.png'), fullPage: true });
});

test('project stages use real status and unknown status is neutral', async ({ page }, info) => {
  await page.goto('/projects/project-one');
  await expect(page.locator('[aria-current="step"]')).toContainText('В работе');
  await expect(page.getByRole('button', { name: 'Отправить решение', exact: true })).toBeVisible();
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('project.png'), fullPage: true });
  await page.route('**/api/v1/projects/project-one', route =>
    route.fulfill({
      json: {
        id: 'project-one',
        case_id: study.id,
        case_version: 1,
        status: 'future-status',
        head_commit_sha: 'head',
      },
    }),
  );
  await page.reload();
  await expect(page.getByText('Статус проекта недоступен.')).toBeVisible();
  await expect(page.locator('[aria-current="step"]')).toHaveCount(0);
});

test('switching account does not show the previous balance when wallet loading fails', async ({
  page,
}) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  await expect(page.getByTestId('credits-balance').locator('.currency-number')).toHaveText('10');
  // SPA history keeps the same service alive, unlike page.goto which would hide stale state.
  await page.goBack();
  await expect(page).toHaveURL(/\/login$/);
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'reviewer-user', role: 'user', capabilities: [] } }),
  );
  await page.route('**/api/v1/me/wallet', route => route.fulfill({ status: 503, json: {} }));
  await page.route('**/api/v1/me/review-mode', route =>
    route.fulfill({
      json: { enabled: false, status: 'disabled', can_review: true },
    }),
  );
  await page.getByRole('button', { name: 'Ревьюер+', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews$/);
  await expect(page.getByTestId('credits-balance').locator('.currency-number')).toHaveText('—');
  await expect(page.getByTestId('credits-balance')).not.toContainText('10');
});

test('expired session offers login and forbidden catalogue stays an error', async ({ page }) => {
  await page.route('**/api/v1/cases?*', route => route.fulfill({ status: 403, json: {} }));
  await page.goto('/catalog');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByText('Опубликованных кейсов пока нет.')).toHaveCount(0);
  await page.route('**/api/v1/cases?*', route => route.fulfill({ status: 401, json: {} }));
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Войти для просмотра кейсов' })).toBeVisible();
});

test('admin search, retained content during refresh and empty/error states', async ({
  page,
}, info) => {
  const record = {
    ...study,
    created_at: '2026-10-17T10:00:00Z',
    version: 1,
    frozen: false,
    status: 'draft',
  };
  await page.route('**/api/v1/admin/cases?*', route =>
    route.fulfill({ json: [record, { ...record, id: 'two', title: 'Платёжная форма' }] }),
  );
  await page.route('**/reference-solution', route => route.fulfill({ status: 404, json: {} }));
  await page.goto('/admin');
  const rows = page.locator('.case-row');
  await expect(rows).toHaveCount(2);
  const search = page.getByRole('searchbox');
  await search.click();
  await search.pressSequentially('платеж');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('Платёжная форма');
  await search.fill('неизвестный');
  await expect(page.getByRole('heading', { name: 'Кейсы не найдены' })).toBeVisible();
  await page.getByRole('button', { name: 'Сбросить поиск', exact: true }).click();
  await expect(rows).toHaveCount(2);
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/api/v1/admin/cases?*', async route => {
    await pending;
    await route.fulfill({ status: 503, json: {} });
  });
  await page.getByRole('combobox', { name: 'Статус', exact: true }).click();
  await page.getByRole('option', { name: 'Черновики', exact: true }).click();
  await expect(page.getByText('Загружаем кейсы…', { exact: true })).toBeVisible();
  await expect(rows).toHaveCount(2);
  release();
  await expect(page.getByRole('alert').first()).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Статус', exact: true })).toHaveValue(
    'Все статусы',
  );
  await expect(page.getByRole('heading', { name: 'Пока нет кейсов' })).toHaveCount(0);
  await page.route('**/api/v1/admin/cases?*', route => route.fulfill({ json: [record] }));
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(rows).toHaveCount(1);
  await expect(page.getByRole('combobox', { name: 'Статус', exact: true })).toHaveValue(
    'Черновики',
  );
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('admin.png'), fullPage: true });
});

test('failed autosave keeps editor text and pending save prevents a duplicate', async ({
  page,
}, info) => {
  const file = { path: 'solution.sql', sha: 'v1', commit_sha: 'head', content: 'SELECT 1;' };
  await page.route('**/api/v1/projects/project-one/files', route =>
    route.fulfill({ json: [file] }),
  );
  let writes = 0;
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/api/v1/projects/project-one/files/content?*', async route => {
    if (route.request().method() === 'PUT') {
      writes++;
      await pending;
      await route.fulfill({ status: 409, json: {} });
    } else await route.fulfill({ json: file });
  });
  await page.goto('/projects/project-one');
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42;');
  await expect.poll(() => writes).toBe(1);
  await expect(page.getByText('Сохраняем…', { exact: true })).toBeVisible();
  await expect(editor).toHaveAttribute('contenteditable', 'true');
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  release();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(editor).toHaveText('SELECT 42;');
  expect(writes).toBe(1);
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('save-error.png'), fullPage: true });
});

test('failed server replacement preserves the editor draft across reload', async ({ page }) => {
  const file = { path: 'solution.sql', sha: 'v1', commit_sha: 'head', content: 'SELECT 1;' };
  let failRead = false;
  await page.route('**/api/v1/projects/project-one/files', route =>
    route.fulfill({ json: [file] }),
  );
  await page.route('**/api/v1/projects/project-one/files/content?*', route => {
    if (route.request().method() === 'PUT') return route.fulfill({ status: 409, json: {} });
    return failRead ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: file });
  });
  await page.goto('/projects/project-one');
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42;');
  await expect(page.getByRole('alert')).toBeVisible();
  failRead = true;
  await page.getByRole('button', { name: 'Загрузить серверную версию файла' }).click();
  await page.getByRole('button', { name: 'Заменить серверной версией' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(editor).toHaveText('SELECT 42;');
  failRead = false;
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(editor).toHaveText('SELECT 42;');
  await expect(page.getByText('Не удалось сохранить', { exact: true })).toBeVisible();
});

test('late failure from previous file does not replace the current file state', async ({
  page,
}) => {
  const files = [
    { path: 'first.sql', sha: 'a1', commit_sha: 'head', content: 'SELECT 1;' },
    { path: 'second.sql', sha: 'b1', commit_sha: 'head', content: 'SELECT 2;' },
  ];
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/api/v1/projects/project-one/files', route => route.fulfill({ json: files }));
  await page.route('**/api/v1/projects/project-one/files/content?*', async route => {
    if (new URL(route.request().url()).searchParams.get('path') === 'first.sql') {
      await pending;
      await route.fulfill({ status: 503, json: {} });
    } else await route.fulfill({ json: files[1] });
  });
  await page.goto('/projects/project-one');
  await page.getByRole('button', { name: 'second.sql', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: second.sql', exact: true });
  await expect(editor).toHaveText('SELECT 2;');
  const failedRead = page.waitForResponse(
    response =>
      new URL(response.url()).searchParams.get('path') === 'first.sql' && response.status() === 503,
  );
  release();
  await (await failedRead).finished();
  await expect(page.locator('app-remote-files')).toHaveAttribute('aria-busy', 'false');
  await expect(editor).toHaveText('SELECT 2;');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  await expect(editor).toHaveAttribute('contenteditable', 'true');
});

test('auth, review and expert screens retain readable layouts and keyboard focus', async ({
  page,
}, info) => {
  await page.route('**/api/v1/me/review-mode', route =>
    route.fulfill({
      json: {
        enabled: false,
        status: 'disabled',
        can_review: true,
        can_assess_readiness: true,
        can_quality_review: true,
      },
    }),
  );
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const path of ['/register', '/login', '/reviews', '/reviews/catalog-search']) {
    await page.goto(path);
    await expect(page.locator('h1')).toBeVisible();
    if (path === '/reviews') await expect(page.getByText('Активных проверок нет.')).toBeVisible();
    if (path === '/reviews/catalog-search')
      await expect(page.getByRole('heading', { name: study.title, exact: true })).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await checkLayout(page);
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
    await page.screenshot({
      path: info.outputPath(path.replaceAll('/', '-') + '.png'),
      fullPage: true,
    });
  }
  await page.evaluate(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'ui-test', email: 'mentor.local@example.com', name: 'Эксперт' }),
    ),
  );
  await page.goto('/expert');
  await expect(page.getByRole('heading', { name: 'Экспертное ревью', exact: true })).toBeVisible();
  await expect(page.getByText('Активных проверок нет.')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('expert.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('performer has one working footer profile and no review access', async ({ page }, info) => {
  const writes: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET' && request.url().includes('/api/v1/'))
      writes.push(request.url());
  });
  await page.goto('/catalog');
  if ((page.viewportSize()?.width ?? 1440) <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  const sidebar = page.locator('app-sidebar');
  await expect(sidebar.getByRole('link', { name: 'Ревью', exact: true })).toHaveCount(0);
  await expect(
    sidebar
      .getByRole('navigation', { name: 'Основная навигация' })
      .getByRole('link', { name: 'Профиль', exact: true }),
  ).toHaveCount(0);
  const profileLink = sidebar.getByRole('link', { name: 'Профиль', exact: true });
  await expect(profileLink).toHaveCount(1);
  await profileLink.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByRole('heading', { name: 'Профиль', exact: true })).toBeVisible();
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('footer-profile.png'), fullPage: true });
  for (const path of [
    '/reviews',
    '/reviews/unknown',
    '/expert',
    '/review-results/unknown',
    '/reviews/unknown/correction/unknown',
  ]) {
    await page.goto(path);
    await expect(page).toHaveURL(/reviewAccess=denied/);
    await expect(page.getByText('Ревью доступно участникам', { exact: false })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Начать ревью', exact: true })).toHaveCount(0);
  }
  expect(writes).toEqual([]);
});

test('catalog level filters combine with search and reset', async ({ page }, info) => {
  await page.route('**/api/v1/cases?*', route => {
    const query = new URL(route.request().url()).searchParams;
    return route.fulfill({
      json: [
        study,
        { ...study, id: 'expert-case', title: 'Экспертный поиск', difficulty: 'expert' },
      ].filter(
        item =>
          (!query.get('difficulty') || item.difficulty === query.get('difficulty')) &&
          item.title.toLocaleLowerCase('ru').includes(query.get('q') ?? ''),
      ),
    });
  });
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows).toHaveCount(2);
  await page.getByRole('button', { name: 'Уровень 3', exact: true }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('Экспертный поиск');
  await page.getByRole('searchbox').fill('несуществующий');
  await expect(rows).toHaveCount(0);
  await expect(page.getByText('Опубликованных кейсов пока нет.')).toHaveCount(0);
  await page.getByRole('button', { name: 'Сбросить фильтры' }).click();
  await expect(rows).toHaveCount(2);
  await expect(page.getByRole('searchbox')).toHaveValue('');
  await checkLayout(page);
  await page.screenshot({ path: info.outputPath('filters.png'), fullPage: true });
});

test('review access failure is retryable and never grants access', async ({ page }) => {
  await page.route('**/api/v1/me/review-mode', route => route.fulfill({ status: 503, json: {} }));
  await page.goto('/reviews');
  await expect(page).toHaveURL(/reviewAccess=unavailable/);
  await expect(
    page.getByText('Не удалось проверить доступ к ревью.', { exact: false }),
  ).toBeVisible();
  await page.route('**/api/v1/me/review-mode', route =>
    route.fulfill({ json: { enabled: false, status: 'disabled', can_review: false } }),
  );
  await page.getByRole('link', { name: 'Повторить проверку доступа' }).click();
  await expect(page).toHaveURL(/reviewAccess=denied/);
  await expect(page.getByText('Ревью доступно участникам', { exact: false })).toBeVisible();
});

test('route progress stays visible while session is checked', async ({ page }) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  await page.route('**/auth/me', async route => {
    await pending;
    await route.fulfill({ json: { id: 'ui-user', role: 'user', capabilities: ['participant'] } });
  });
  await page.goto('/profile');
  await expect(page.getByRole('progressbar', { name: 'Открываем страницу…' })).toBeVisible();
  release();
  await expect(page.getByRole('heading', { name: 'Профиль', exact: true })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Открываем страницу…' })).toHaveCount(0);
});
