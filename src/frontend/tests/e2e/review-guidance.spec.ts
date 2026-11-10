import { expect, Page, test } from '@playwright/test';
import { emptyAttachments } from './fixtures/attachments';

const task = {
  id: 'guided-review',
  submission_id: 'guided-submission',
  kind: 'review',
  review_id: null,
  revision_id: null,
  status: 'assigned',
  lease_expires_at: '2030-10-18T10:00:00Z',
};

async function fixture(
  page: Page,
  options: { active?: boolean; expired?: boolean; empty?: boolean; saveError?: boolean } = {},
): Promise<{ mutations: string[] }> {
  const mutations: string[] = [];
  let enabled = false;
  let assignedTask = {
    ...task,
    lease_expires_at: options.expired ? '2020-01-01T00:00:00Z' : task.lease_expires_at,
  };
  let draft = {
    version: 1,
    score: null as number | null,
    verdict: null as string | null,
    feedback: { strengths: '', problems: '', next_steps: '' },
    comments: [] as unknown[],
  };
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({
        token: 'guided-test',
        email: 'review-plus.local@example.com',
        name: 'Ревьюер+',
      }),
    ),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'guided-user', role: 'user', capabilities: ['participant'] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('attachments'))
      return route.fulfill({ json: emptyAttachments(draft.version) });
    const method = route.request().method();
    if (method !== 'GET') mutations.push(`${method} ${path}`);
    if (path.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (path.endsWith('/review-mode')) {
      if (method === 'PUT') enabled = route.request().postDataJSON().enabled;
      return route.fulfill({ json: { enabled, status: 'available', can_review: true } });
    }
    if (path === '/api/v1/me/review-assignments') {
      if (method === 'POST') {
        assignedTask = task;
        return options.empty
          ? route.fulfill({ status: 204 })
          : route.fulfill({ status: 201, json: task });
      }
      return route.fulfill({ json: options.active || options.expired ? [assignedTask] : [] });
    }
    if (path === '/api/v1/me/reviews')
      return route.fulfill({ json: { items: [], next_offset: null } });
    if (path.endsWith('/workspace'))
      return route.fulfill({
        json: {
          assignment: assignedTask,
          draft,
          case: {
            title: 'Проверка SQL-запроса',
            description: 'Проверьте, что запрос возвращает число 2.',
            expected_result: 'Одна строка со значением 2.',
          },
        },
      });
    if (path.endsWith('/draft')) {
      if (method === 'PUT') {
        if (options.saveError)
          return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
        const body = route.request().postDataJSON();
        expect(body.expected_version).toBe(draft.version);
        draft = { ...body, version: draft.version + 1 };
      }
      return route.fulfill({ json: draft });
    }
    if (path === '/api/v1/review-assignments/guided-review/reviews') {
      expect(route.request().postDataJSON().expected_draft_version).toBe(draft.version);
      return route.fulfill({
        status: 201,
        json: { id: 'guided-result', submission_id: task.submission_id, status: 'published' },
      });
    }
    if (path === '/api/v1/reviews/guided-result')
      return route.fulfill({
        json: {
          id: 'guided-result',
          submission_id: task.submission_id,
          assignment_id: task.id,
          reviewer_id: 'guided-user',
          status: 'published',
          current_revision: { ...draft, id: 'revision-one' },
        },
      });
    if (path === '/api/v1/review-assignments/guided-review')
      return route.fulfill({ json: assignedTask });
    if (path.endsWith('/files') || path.endsWith('/diff'))
      return route.fulfill({ json: { entries: [], next_offset: null } });
    if (path.endsWith('/quality-assessments'))
      return route.fulfill({ json: { items: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  return { mutations };
}

async function feedback(page: Page): Promise<void> {
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByLabel('Оценка от 0 до 100').fill('85');
  await page.getByLabel('Вердикт', { exact: true }).click();
  await page.getByRole('listbox').getByRole('option', { name: 'Принято', exact: true }).click();
  await page
    .getByLabel('Сильные стороны', { exact: true })
    .fill('Запрос возвращает ожидаемое число.');
  await page.getByLabel('Проблемы', { exact: true }).fill('Критичных ошибок нет.');
  await page.getByLabel('Следующие шаги', { exact: true }).fill('Добавьте пояснение к запросу.');
}

test('cancelled logout keeps the session and unsaved review; confirmed logout revokes it once', async ({
  page,
}) => {
  await fixture(page, { active: true });
  let signouts = 0;
  await page.route('**/auth/sign_out', route => {
    signouts++;
    return route.fulfill({ status: 204 });
  });
  await page.goto('/reviews/guided-review');
  await feedback(page);
  const openNavigation = async (): Promise<void> => {
    if ((page.viewportSize()?.width ?? 1440) <= 620)
      await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  };
  await openNavigation();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/guided-review$/);
  await expect(page.getByLabel('Сильные стороны', { exact: true })).toHaveValue(
    'Запрос возвращает ожидаемое число.',
  );
  expect(signouts).toBe(0);
  expect(await page.evaluate(() => !!sessionStorage.getItem('prodguild.api.session.v1'))).toBe(
    true,
  );
  await openNavigation();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(signouts).toBe(1);
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBeNull();
});

test('failed logout retains the session and review for an explicit retry', async ({ page }) => {
  await fixture(page, { active: true });
  let attempts = 0;
  await page.route('**/auth/sign_out', route => {
    attempts++;
    return attempts === 1
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ status: 204 });
  });
  await page.goto('/reviews/guided-review');
  await feedback(page);
  const logout = async (): Promise<void> => {
    if ((page.viewportSize()?.width ?? 1440) <= 620)
      await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  };
  await logout();
  await expect(page.getByRole('alert')).toContainText('Сервис временно недоступен');
  await expect(page).toHaveURL(/\/reviews\/guided-review$/);
  expect(await page.evaluate(() => !!sessionStorage.getItem('prodguild.api.session.v1'))).toBe(
    true,
  );
  await expect(page.getByLabel('Сильные стороны', { exact: true })).toHaveValue(
    'Запрос возвращает ожидаемое число.',
  );
  await logout();
  await expect(page).toHaveURL(/\/login$/);
  expect(attempts).toBe(2);
});

test('one start action enables reviewing, opens work and sends the current draft after confirmation', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto('/reviews');
  await page.getByRole('button', { name: 'Начать ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/guided-review$/);
  expect(state.mutations.slice(0, 2)).toEqual([
    'PUT /api/v1/me/review-mode',
    'POST /api/v1/me/review-assignments',
  ]);
  await expect(page.getByRole('heading', { name: 'Задание', exact: true })).toBeVisible();
  await feedback(page);
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }),
  ).toBeVisible();
  expect(state.mutations).toContain('PUT /api/v1/review-assignments/guided-review/draft');
  expect(state.mutations).not.toContain('POST /api/v1/review-assignments/guided-review/reviews');
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/review-results\/guided-result$/);
  expect(state.mutations.filter(path => path.endsWith('/guided-review/reviews'))).toHaveLength(1);
});

test('existing assignment has a clear resume action without claiming again', async ({ page }) => {
  const state = await fixture(page, { active: true });
  await page.goto('/reviews');
  await page.getByRole('button', { name: 'Продолжить ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/guided-review$/);
  expect(state.mutations).toEqual([]);
});

test('empty queue explains that no work is available and leaves a retry', async ({ page }) => {
  await fixture(page, { empty: true });
  await page.goto('/reviews');
  await page.getByRole('button', { name: 'Начать ревью', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: /нет|пока/i })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Начать ревью', exact: true })).toBeEnabled();
});

test('missing feedback explains required fields instead of silently disabling send', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto('/reviews/guided-review');
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  const send = page.getByRole('button', { name: 'Отправить ревью', exact: true });
  await expect(send).toBeEnabled();
  await send.click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }),
  ).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

test('draft conflict retains entered feedback and blocks publication', async ({ page }) => {
  const state = await fixture(page, { saveError: true });
  await page.goto('/reviews/guided-review');
  await feedback(page);
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('другой вкладке');
  await expect(page.getByLabel('Сильные стороны', { exact: true })).toHaveValue(
    'Запрос возвращает ожидаемое число.',
  );
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }),
  ).toHaveCount(0);
  expect(state.mutations).not.toContain('POST /api/v1/review-assignments/guided-review/reviews');
});

test('legacy past timestamp still offers continuing the same assignment', async ({ page }) => {
  const state = await fixture(page, { expired: true });
  await page.goto('/reviews');
  await page.getByRole('button', { name: 'Продолжить ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/guided-review$/);
  expect(state.mutations).toEqual([]);
  await expect(page.getByText('На проверку — 24 часа.', { exact: true })).toHaveCount(0);
});

test('pending published review refreshes automatically after an expert publishes it', async ({
  page,
}) => {
  await fixture(page);
  await page.clock.install();
  let status = 'pending_quality_check';
  await page.route('**/api/v1/reviews/guided-result', route =>
    route.fulfill({
      json: {
        id: 'guided-result',
        submission_id: task.submission_id,
        assignment_id: task.id,
        reviewer_id: 'guided-user',
        status,
        current_revision: {
          id: 'revision-one',
          score: 85,
          verdict: 'approved',
          comments: [],
          feedback: { strengths: 'Проверено', problems: 'Нет', next_steps: 'Продолжайте' },
        },
      },
    }),
  );
  await page.goto('/review-results/guided-result');
  await expect(page.getByRole('region', { name: 'Ревью на проверке', exact: true })).toBeVisible();
  status = 'published';
  await page.clock.fastForward(31_000);
  await expect(
    page.getByRole('region', { name: 'Опубликованное ревью', exact: true }),
  ).toBeVisible();
});

test('queue focus refreshes review rights and disables claiming after revocation', async ({
  page,
}) => {
  await fixture(page);
  let canReview = true;
  await page.route('**/api/v1/me/review-mode', route =>
    route.fulfill({
      json: {
        enabled: true,
        status: 'waiting',
        can_review: canReview,
        tasks: [],
      },
    }),
  );
  await page.goto('/reviews');
  const start = page.getByRole('button', { name: 'Начать ревью', exact: true });
  await expect(start).toBeEnabled();
  canReview = false;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(start).toBeDisabled();
  await expect(page.getByText('Сейчас у вас нет прав на новые проверки.')).toBeVisible();
});

test('focus does not reload an active assignment or replace unsaved reviewer feedback', async ({
  page,
}) => {
  await fixture(page, { active: true });
  let workspaceReads = 0;
  page.on('request', request => {
    if (request.url().endsWith('/workspace')) workspaceReads++;
  });
  await page.goto('/reviews/guided-review');
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  const strengths = page.getByLabel('Сильные стороны', { exact: true });
  await strengths.fill('Мой несохранённый текст.');
  const reads = workspaceReads;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(strengths).toHaveValue('Мой несохранённый текст.');
  expect(workspaceReads).toBe(reads);
});

for (const [role, capabilities] of [
  ['reviewer', []],
  ['organizer', ['organizer']],
] as const) {
  test(`revoked ${role} can return an assignment even when history is forbidden`, async ({
    page,
  }) => {
    await fixture(page, { active: true });
    await page.route('**/auth/me', route =>
      route.fulfill({ json: { id: 'guided-user', role: 'user', capabilities } }),
    );
    let released = false;
    await page.route('**/api/v1/me/review-mode', route =>
      route.fulfill({
        json: {
          enabled: true,
          status: 'assigned',
          can_review: false,
          tasks: released ? [] : [{ ...task, can_continue: false }],
        },
      }),
    );
    await page.route('**/api/v1/me/reviews?*', route =>
      route.fulfill({ status: 403, json: { code: 'FORBIDDEN' } }),
    );
    await page.route('**/api/v1/me/review-assignments?*', route =>
      route.fulfill({ json: released ? [] : [{ ...task, can_continue: false }] }),
    );
    await page.route('**/api/v1/review-assignments/guided-review', route => {
      if (route.request().method() === 'DELETE') {
        released = true;
        return route.fulfill({ status: 204 });
      }
      return route.fulfill({ json: { ...task, can_continue: false } });
    });
    await page.goto('/reviews');
    await expect(
      page.getByText('Проверка недоступна. Верните её в очередь.', { exact: true }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Вернуть в очередь', exact: true }).click();
    await page.getByRole('button', { name: 'Подтвердить возврат', exact: true }).click();
    await expect.poll(() => released).toBe(true);
    await expect(page.getByRole('button', { name: 'Вернуть в очередь', exact: true })).toHaveCount(
      0,
    );
  });
}
