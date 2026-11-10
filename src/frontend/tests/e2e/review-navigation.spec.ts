import { expect, Page, test } from '@playwright/test';

async function fixture(page: Page, email = 'navigation@example.com') {
  const task = {
    id: 'ordinary-task',
    kind: 'review',
    submission_id: 'submission-one',
    review_id: null,
    revision_id: null,
    status: 'assigned',
    can_continue: true,
    lease_expires_at: null,
  };
  const state = {
    can_review: true,
    can_assess_readiness: true,
    tasks: [] as (typeof task)[],
    ownedTasks: [task],
    modeStatus: 200,
    resourceStatus: 404,
    modeOverride: undefined as unknown,
    reads: [] as string[],
    mutations: [] as string[],
    claims: [] as string[],
    lists: [] as string[],
  };
  await page.addInitScript(
    email =>
      sessionStorage.setItem(
        'prodguild.api.session.v1',
        JSON.stringify({
          token: 'navigation-token',
          email,
          name: 'Участник',
        }),
      ),
    email,
  );
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'navigation-owner',
        role: 'user',
        capabilities: ['participant'],
      },
    }),
  );
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (route.request().method() !== 'GET')
      state.mutations.push(`${route.request().method()} ${path}`);
    state.reads.push(path);
    if (path.endsWith('/me/review-mode'))
      return route.fulfill({
        status: state.modeStatus,
        json: state.modeOverride ?? {
          enabled: true,
          status: 'waiting',
          can_review: state.can_review,
          can_assess_readiness: state.can_assess_readiness,
          tasks: state.tasks,
        },
      });
    if (path.endsWith('/me/review-assignments')) {
      const kind = url.searchParams.get('kind') ?? '';
      if (route.request().method() === 'POST') {
        state.claims.push(kind);
        return route.fulfill({ json: null });
      }
      state.lists.push(kind);
      // Master returns the shared assignment list; kind does not select a queue.
      return route.fulfill({ json: state.tasks });
    }
    if (/^\/api\/v1\/review-assignments\/[^/]+$/.test(path)) {
      const owned = state.ownedTasks.find(value => path.endsWith('/' + value.id));
      return owned
        ? route.fulfill({ json: owned })
        : route.fulfill({ status: state.resourceStatus, json: { code: 'NOT_FOUND' } });
    }
    if (path.endsWith('/workspace'))
      return route.fulfill({
        json: {
          assignment: task,
          case: {
            title: 'SQL и Python',
            description: 'Проверьте решение',
            expected_result: 'Результат',
          },
          draft: {
            version: 1,
            score: null,
            verdict: null,
            feedback: { strengths: '', problems: '', next_steps: '' },
            comments: [],
          },
        },
      });
    if (path.endsWith('attachments'))
      return route.fulfill({
        json: {
          version: path.includes('/projects/') ? 0 : 1,
          items: [],
          total_bytes: 0,
          max_file_bytes: 10 * 1024 * 1024,
          max_total_bytes: 50 * 1024 * 1024,
          max_files: 20,
        },
      });
    if (path === '/api/v1/reviews/foreign')
      return route.fulfill({ status: state.resourceStatus, json: { code: 'NOT_FOUND' } });
    if (path === '/api/v1/reviews/published-one')
      return route.fulfill({
        json: {
          id: 'published-one',
          assignment_id: task.id,
          reviewer_id: 'navigation-owner',
          submission_id: task.submission_id,
          status: 'published',
          current_revision: {
            id: 'revision-one',
            score: 80,
            verdict: 'approved',
            feedback: { strengths: 'Сохранённый результат', problems: '', next_steps: '' },
            comments: [],
          },
        },
      });
    if (path.endsWith('/me/wallet'))
      return route.fulfill({ json: { available: 10, settled: 10, reserved: 0 } });
    if (path.endsWith('/me/reviews') || path.endsWith('/reviewer-applications'))
      return route.fulfill({ json: { items: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  return { state, task };
}

async function navigation(page: Page) {
  if ((page.viewportSize()?.width ?? 1440) <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  return page.getByRole('navigation', { name: 'Основная навигация', exact: true });
}

test('A2: expert server rights expose separate views without choosing a queue', async ({
  page,
}) => {
  const { state } = await fixture(page);
  await page.goto('/expert');
  let nav = await navigation(page);
  await expect(nav.getByRole('link')).toHaveText([
    'Каталог кейсов',
    'В работе',
    'Ревью',
    'Экспертное ревью',
    'Заявки на эксперта',
  ]);
  await expect(nav.getByRole('link', { name: 'Экспертное ревью', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Начать ревью', exact: true })).toBeEnabled();
  nav = await navigation(page);
  await nav.getByRole('link', { name: 'Ревью', exact: true }).click();
  await expect(page).toHaveTitle('Ревью');
  await expect(page.getByRole('button', { name: 'Начать ревью', exact: true })).toBeEnabled();
  nav = await navigation(page);
  await nav.getByRole('link', { name: 'Заявки на эксперта', exact: true }).click();
  await expect(page).toHaveURL(/\/expert\/applications$/);
  nav = await navigation(page);
  await expect(nav.getByRole('link', { name: 'Заявки на эксперта', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(state.mutations).toEqual([]);
});

test('one active ordinary task is continued from the expert queue without another claim', async ({
  page,
}) => {
  const { state, task } = await fixture(page);
  state.tasks = [task];
  await page.goto('/expert');
  await expect(page.getByText('У вас уже есть обычное ревью.', { exact: false })).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Назначенные ревью', exact: true }).getByRole('link'),
  ).toHaveCount(0);
  await page.getByRole('button', { name: 'Продолжить ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/ordinary-task$/);
  await expect(
    page.getByRole('heading', { name: 'SQL и Python', exact: true }).first(),
  ).toBeVisible();
  expect(state.claims).toEqual([]);
});

test('ordinary assignment cannot grant access to the expert applications inbox', async ({
  page,
}) => {
  const { state, task } = await fixture(page);
  state.can_assess_readiness = false;
  state.tasks = [task];
  await page.goto('/expert/applications');
  await expect(page).toHaveURL(/\/catalog\?reviewAccess=denied$/);
  const nav = await navigation(page);
  await expect(nav.getByRole('link', { name: 'Заявки на эксперта', exact: true })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'Экспертное ревью', exact: true })).toHaveCount(0);
});

test('an ordinary assignment in an old expert URL opens its canonical ordinary route', async ({
  page,
}) => {
  const { state, task } = await fixture(page);
  state.can_assess_readiness = false;
  state.tasks = [task];
  await page.goto('/expert/ordinary-task');
  await expect(page).toHaveURL(/\/reviews\/ordinary-task$/);
  await expect(
    page.getByRole('heading', { name: 'SQL и Python', exact: true }).first(),
  ).toBeVisible();
  const nav = await navigation(page);
  await expect(nav.getByRole('link', { name: 'Ревью', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(nav.getByRole('link', { name: 'Экспертное ревью', exact: true })).toHaveCount(0);
  expect(state.claims).toEqual([]);
});

test('promotion and revocation refresh navigation without signing in again', async ({ page }) => {
  await page.clock.install();
  const { state } = await fixture(page);
  state.can_review = state.can_assess_readiness = false;
  await page.goto('/catalog');
  await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
  const nav = await navigation(page);
  await expect(nav.getByRole('link', { name: 'Ревью', exact: true })).toHaveCount(0);
  state.can_review = true;
  await page.clock.fastForward(31_000);
  await expect(nav.getByRole('link', { name: 'Ревью', exact: true })).toBeVisible();
  state.can_assess_readiness = true;
  await page.clock.fastForward(31_000);
  await expect(nav.getByRole('link', { name: 'Экспертное ревью', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Заявки на эксперта', exact: true })).toBeVisible();
  state.can_review = state.can_assess_readiness = false;
  await page.clock.fastForward(31_000);
  await expect(nav.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе']);
  expect(state.mutations).toEqual([]);
});

for (const kind of ['review', 'quality_check'] as const) {
  const path = kind === 'review' ? '/reviews' : '/expert';
  const label = kind === 'review' ? 'Ревью' : 'Экспертное ревью';
  test(`A2: revoked ${kind} keeps its assignment view and metadata without protected materials`, async ({
    page,
  }) => {
    const { state, task } = await fixture(page);
    state.can_review = state.can_assess_readiness = false;
    const revoked = { ...task, id: `${kind}-revoked`, kind, can_continue: false };
    state.tasks = state.ownedTasks = [revoked];
    await page.goto(path);
    const nav = await navigation(page);
    await expect(nav.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе', label]);
    if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
    await page.goto(`${path}/${revoked.id}`);
    await expect(page.getByRole('heading', { name: 'Проверка недоступна' })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Вернуть в очередь', exact: true }),
    ).toBeEnabled();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Проверка недоступна' })).toBeVisible();
    await page.goto('/expert/applications');
    await expect(page).toHaveURL(/\/catalog\?reviewAccess=denied$/);
    expect(state.reads.some(value => /\/(workspace|source-review|draft)$/.test(value))).toBe(false);
    expect(state.mutations).toEqual([]);
  });
}

test('A2: a quality assignment in an ordinary URL uses its server kind', async ({ page }) => {
  const { state, task } = await fixture(page);
  state.can_review = state.can_assess_readiness = false;
  state.tasks = state.ownedTasks = [
    { ...task, id: 'quality-task', kind: 'quality_check', can_continue: false },
  ];
  await page.goto('/reviews/quality-task');
  await expect(page).toHaveURL(/\/expert\/quality-task$/);
  await expect(page.getByRole('heading', { name: 'Проверка недоступна' })).toBeVisible();
  const nav = await navigation(page);
  await expect(nav.getByRole('link', { name: 'Экспертное ревью', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(state.mutations).toEqual([]);
});

test('A2: closed assignments and owned published results do not require current queue rights', async ({
  page,
}) => {
  const { state, task } = await fixture(page);
  state.can_review = state.can_assess_readiness = false;
  state.ownedTasks = [{ ...task, status: 'completed', can_continue: false }];
  for (const path of [
    '/reviews/ordinary-task',
    '/reviews/ordinary-task/correction/published-one',
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Проверка закрыта' })).toBeVisible();
  }
  await page.goto('/review-results/published-one');
  await expect(
    page.getByRole('heading', { name: 'Опубликованное ревью', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Сохранённый результат', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Сохранённый результат', { exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]);
});

for (const path of ['/reviews/foreign', '/expert/foreign', '/reviews/foreign/correction/foreign']) {
  test(`A2: server ownership denies ${path} without private content`, async ({ page }) => {
    const { state } = await fixture(page);
    state.can_review = state.can_assess_readiness = false;
    await page.goto(path);
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole('alert')).toContainText('Данные пока не найдены');
    await expect(page.getByRole('button', { name: 'Вернуть в очередь', exact: true })).toHaveCount(
      0,
    );
    expect(state.reads).toContain('/api/v1/review-assignments/foreign');
    expect(state.reads.some(value => /\/(workspace|source-review|draft)$/.test(value))).toBe(false);
    expect(state.mutations).toEqual([]);
  });
}

for (const failure of ['503', 'malformed'] as const) {
  test(`A2: ${failure} discards stale assignment access and retry reads fresh rights`, async ({
    page,
  }) => {
    await page.clock.install();
    const { state, task } = await fixture(page);
    state.can_review = state.can_assess_readiness = false;
    state.tasks = [
      failure === '503' ? task : { ...task, id: 'quality-task', kind: 'quality_check' },
    ];
    await page.goto('/catalog');
    let nav = await navigation(page);
    await expect(
      nav.getByRole('link', {
        name: failure === '503' ? 'Ревью' : 'Экспертное ревью',
        exact: true,
      }),
    ).toBeVisible();
    if (failure === '503') state.modeStatus = 503;
    else state.modeOverride = { can_review: false, can_assess_readiness: false, tasks: {} };
    await page.clock.fastForward(31_000);
    await expect(nav.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе']);
    if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
    for (const path of ['/reviews', '/expert', '/expert/applications']) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/catalog\?reviewAccess=unavailable$/);
    }
    state.modeStatus = 200;
    state.modeOverride = undefined;
    state.tasks = [];
    state.can_review = true;
    await page.getByRole('link', { name: 'Повторить проверку доступа', exact: true }).click();
    await expect(page).toHaveURL(/\/reviews$/);
    nav = await navigation(page);
    await expect(nav.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе', 'Ревью']);
    expect(state.mutations).toEqual([]);
  });
}

test('A2: legacy expert email, Grade and unsupported field cannot grant rights', async ({
  page,
}) => {
  const { state } = await fixture(page, 'mentor.local@example.com');
  state.modeOverride = {
    enabled: false,
    status: 'off',
    can_review: false,
    can_assess_readiness: false,
    can_quality_review: true,
    tasks: [],
  };
  await page.addInitScript(() => {
    localStorage.setItem('prodguild.user.v1', JSON.stringify({ login: 'demo', grade: 'expert' }));
  });
  for (const path of ['/reviews', '/expert', '/expert/applications']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/catalog\?reviewAccess=denied$/);
  }
  expect(
    await page.evaluate(
      () => JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}').email,
    ),
  ).toBe('mentor.local@example.com');
  expect(await page.evaluate(() => localStorage.getItem('prodguild.user.v1'))).toBe(
    JSON.stringify({ login: 'demo', grade: 'expert' }),
  );
  const nav = await navigation(page);
  await expect(nav.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе']);
  expect(state.mutations).toEqual([]);
});

for (const status of [403, 404]) {
  test(`A2: published resource ${status} shows no private content`, async ({ page }) => {
    const { state } = await fixture(page);
    state.can_review = state.can_assess_readiness = false;
    state.resourceStatus = status;
    await page.goto('/review-results/foreign');
    await expect(page).toHaveURL(/\/review-results\/foreign$/);
    await expect(page.getByRole('alert')).toContainText(
      status === 403 ? 'Для ревью нужны подтверждённые права' : 'Данные пока не найдены',
    );
    await expect(page.getByText('Сохранённый результат', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('form')).toHaveCount(0);
    expect(state.reads).toContain('/api/v1/reviews/foreign');
    expect(state.reads.some(value => /\/(workspace|source-review|draft)$/.test(value))).toBe(false);
    expect(state.mutations).toEqual([]);
  });
}
