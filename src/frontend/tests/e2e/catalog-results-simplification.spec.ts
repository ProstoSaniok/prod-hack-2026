import { expect, Page, test } from '@playwright/test';

const study = {
  id: 'case-one',
  title: 'Анализ заказов',
  description: 'Найдите повторные покупки в истории заказов.',
  expected_result: 'SQL-запрос и выводы по результатам.',
  difficulty: 'review_minus',
  content_version: 7,
  competencies: [{ competency_id: 'sql', required_value: 20, weight: 0.1 }],
};
const submission = {
  id: 'solution-one',
  project_id: 'project-one',
  case_id: study.id,
  case_version: 7,
  status: 'submitted',
  submitted_at: '2026-10-17T10:00:00Z',
  commit_sha: 'private-solution-sha',
  base_commit_sha: 'private-base-sha',
  summary: 'Проверила запрос на заказах за октябрь.',
};
const review = {
  id: 'review-one',
  assignment_id: 'assignment-one',
  reviewer_id: 'reviewer-one',
  submission_id: submission.id,
  status: 'published',
  current_revision: {
    id: 'revision-one',
    score: 80,
    verdict: 'approved',
    feedback: {
      strengths: 'Правильно выбраны повторные покупки.',
      problems: 'Ошибок нет.',
      next_steps: 'Добавьте разрез по месяцам.',
    },
    comments: [],
  },
};

async function fixture(page: Page) {
  const state = {
    published: false,
    skillOffsets: [] as string[],
    transactionOffsets: [] as string[],
    writes: [] as string[],
  };
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'catalog-test', email: 'reader@example.com', name: 'Участник' }),
    );
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'reader', role: 'user', capabilities: [] } }),
  );
  await page.route('**/api/v1/**', route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== 'GET') state.writes.push(url.pathname);
    if (url.pathname === '/api/v1/me/review-mode')
      return route.fulfill({ json: { enabled: false, status: 'disabled', can_review: false } });
    if (url.pathname === '/api/v1/me/wallet')
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (url.pathname === '/api/v1/me/wallet/transactions') {
      const offset = url.searchParams.get('offset') ?? '0';
      state.transactionOffsets.push(offset);
      return route.fulfill({
        json:
          offset === '0'
            ? Array.from({ length: 20 }, (_, index) => ({
                id: `other-case-${index}`,
                sequence: 21 - index,
                kind: 'case_reward',
                source_id: `successful_case:another-case-${index}:reader`,
                settled_delta: 3,
                reserved_delta: 0,
                created_at: '2026-10-17T10:00:00Z',
              }))
            : [
                {
                  id: 'case-reward',
                  sequence: 1,
                  kind: 'case_reward',
                  source_id: 'successful_case:case-one:reader',
                  settled_delta: 3,
                  reserved_delta: 0,
                  created_at: '2026-10-16T10:00:00Z',
                },
              ],
      });
    }
    if (url.pathname === '/api/v1/competencies') {
      const offset = url.searchParams.get('offset') ?? '0';
      state.skillOffsets.push(offset);
      return route.fulfill({
        json:
          offset === '0'
            ? Array.from({ length: 20 }, (_, index) => ({
                id: `skill-${index}`,
                name: `Навык ${index}`,
                description: '',
              }))
            : [{ id: 'sql', name: 'SQL', description: '' }],
      });
    }
    if (url.pathname === '/api/v1/cases')
      return route.fulfill({
        json: [
          study,
          { ...study, id: 'case-two', title: 'Анализ возвратов', difficulty: 'review_plus' },
          {
            ...study,
            id: 'case-three',
            title: 'Анализ сезонности',
            difficulty: 'expert',
            competencies: [{ competency_id: 'sql', required_value: 80, weight: 0.25 }],
          },
        ].filter(
          item =>
            !url.searchParams.get('difficulty') ||
            item.difficulty === url.searchParams.get('difficulty'),
        ),
      });
    if (url.pathname === '/api/v1/cases/case-one') return route.fulfill({ json: study });
    if (url.pathname === '/api/v1/cases/case-three')
      return route.fulfill({ json: { ...study, id: 'case-three', difficulty: 'expert' } });
    if (url.pathname === '/api/v1/me/projects' && request.method() === 'POST')
      return route.fulfill({
        json: {
          id: 'project-one',
          case_id: request.postDataJSON().case_id,
          case_version: 7,
          status: 'active',
          head_commit_sha: 'head',
        },
      });
    if (url.pathname === '/api/v1/projects/project-one')
      return route.fulfill({
        json: {
          id: 'project-one',
          case_id: 'case-three',
          case_version: 7,
          status: 'active',
          head_commit_sha: 'head',
        },
      });
    if (url.pathname === '/api/v1/submissions/solution-one')
      return route.fulfill({ json: submission });
    if (url.pathname.endsWith('/feedback'))
      return route.fulfill({ json: { reviews: state.published ? [review] : [] } });
    if (url.pathname.endsWith('/progression'))
      return route.fulfill({
        json: {
          contributions: state.published
            ? [
                {
                  competency_id: 'sql',
                  contribution: 9.23456,
                  displayed_value_before: 10,
                  displayed_value_after: 19.23,
                },
              ]
            : [],
        },
      });
    if (url.pathname === '/api/v1/submissions/solution-one/files')
      return route.fulfill({
        json: { entries: [{ path: 'solution.sql', sha: 'file-sha' }], next_offset: null },
      });
    if (url.pathname.endsWith('/files/content'))
      return route.fulfill({
        json: {
          path: 'solution.sql',
          sha: 'file-sha',
          commit_sha: 'private-solution-sha',
          content: 'SELECT customer_id FROM orders;',
        },
      });
    if (url.pathname.endsWith('/diff'))
      return route.fulfill({ json: { entries: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  return state;
}

async function checkMobileHeading(page: Page): Promise<void> {
  if ((page.viewportSize()?.width ?? 1440) > 620) return;
  const menu = await page
    .getByRole('button', { name: 'Показать боковую панель', exact: true })
    .boundingBox();
  const intro = await page.locator('.page-intro').boundingBox();
  const heading = await page.locator('h1').boundingBox();
  expect(menu).not.toBeNull();
  expect(intro).not.toBeNull();
  expect(heading).not.toBeNull();
  if (menu && intro && heading) {
    expect(Math.abs(menu.y - intro.y)).toBeLessThanOrEqual(20);
    expect(heading.x).toBeGreaterThanOrEqual(menu.x + menu.width);
    const backLink = page.locator('.page-intro a');
    if (await backLink.count()) {
      const back = await backLink.boundingBox();
      expect(back).not.toBeNull();
      if (back) expect(back.x).toBeGreaterThanOrEqual(menu.x + menu.width);
    }
  }
}

test('catalog uses numeric levels and paginated skill names with honest reward previews', async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText('Уровень 1');
  await expect(rows.first()).toContainText('SQL: до +12 XP');
  await expect(rows.first().locator('.reward .currency-number')).toHaveText('+3');
  await expect(rows.first().locator('.reward img')).toBeVisible();
  await expect(rows.first()).toHaveAccessibleName(/PRODита/);
  await expect(rows.first()).toContainText('За первое успешное решение');
  await expect(rows.nth(2)).toContainText('SQL: до +30 XP');
  await checkMobileHeading(page);
  expect(state.skillOffsets).toEqual(['0', '20']);
  await expect(page.getByRole('button', { name: 'Ревьюер−', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Эксперт', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Уровень 3', exact: true }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('Анализ сезонности');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath('catalog-levels.png'), fullPage: true });
  await rows.first().click();
  await expect(page.getByRole('heading', { name: 'Уровень 3', exact: true })).toBeVisible();
  await checkMobileHeading(page);
  await expect(page.getByRole('button', { name: 'Решать кейс', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Решать кейс', exact: true }).click();
  await expect(page).toHaveURL(/\/projects\/project-one$/);
  expect(state.writes).toEqual(['/api/v1/me/projects']);
});

test('submission shows real title, result and exact skill gain without technical data', async ({
  page,
}, info) => {
  const state = await fixture(page);
  await page.goto('/submissions/solution-one');
  await expect(page.getByRole('heading', { name: study.title, exact: true })).toBeVisible();
  await expect(page.locator('.submission-meta')).toContainText('На проверке');
  await checkMobileHeading(page);
  await expect(page.getByText('За этот кейс получено:', { exact: false })).toHaveCount(0);
  expect(state.transactionOffsets).toEqual([]);
  await expect(page.getByText(submission.summary)).toBeVisible();
  await expect(page.getByText('Версии файлов', { exact: true })).toHaveCount(0);
  await expect(page.getByText('private-solution-sha')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Скачать снимок JSON' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Обновить/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
  await expect(editor).toContainText('SELECT customer_id FROM orders;');
  await expect(editor).toHaveAttribute('contenteditable', 'false');
  state.published = true;
  await page.waitForTimeout(1100);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('.submission-meta')).toContainText('Принято');
  await expect(page.getByText(review.current_revision.feedback.strengths)).toBeVisible();
  const gains = page.getByRole('region', { name: 'Изменение навыков' });
  await expect(gains).toContainText('SQL: +9,23456 XP');
  await expect(gains).toContainText('10 → 19,23 XP');
  await expect(page.locator('.case-reward .currency-number')).toHaveText('+3');
  await expect(page.locator('.case-reward img')).toBeVisible();
  await expect(page.locator('.case-reward .currency-unit')).toHaveText('PRODита');
  expect(state.transactionOffsets).toEqual(['0', '20']);
  const fileOrder = await page.locator('app-snapshot-viewer').evaluate(viewer => {
    const feedback = document.querySelector('app-review-feedback');
    return (
      feedback !== null &&
      !!(viewer.compareDocumentPosition(feedback) & Node.DOCUMENT_POSITION_FOLLOWING)
    );
  });
  expect(fileOrder).toBe(true);
  await expect(page.getByRole('heading', { name: 'Комментарии', exact: true })).toHaveCount(0);
  expect(state.writes).toEqual([]);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath('submission-result.png'), fullPage: true });
});

test('switching snapshot version prevents clicks until the new file list is ready', async ({
  page,
}) => {
  await fixture(page);
  let release = () => {};
  const pending = new Promise<void>(resolve => (release = resolve));
  await page.route('**/api/v1/submissions/solution-one/files?*', async route => {
    const base = new URL(route.request().url()).searchParams.get('side') === 'base';
    if (base) await pending;
    return route.fulfill({
      json: {
        entries: [{ path: base ? 'base.sql' : 'solution.sql', sha: 'file-sha' }],
        next_offset: null,
      },
    });
  });
  await page.goto('/submissions/solution-one');
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  const original = page.getByRole('button', { name: 'Исходные файлы', exact: true });
  await original.click();
  const tree = page.locator('app-snapshot-viewer app-file-tree');
  await expect(tree).toHaveAttribute('inert', '');
  await expect(tree).toHaveAttribute('aria-busy', 'true');
  release();
  await expect(original).toHaveAttribute('aria-pressed', 'true');
  await expect(tree).not.toHaveAttribute('inert', '');
  await page.getByRole('button', { name: 'base.sql', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: base.sql', exact: true }),
  ).toContainText('SELECT customer_id FROM orders;');
});

test('unavailable case does not hide an owned submission or its files', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/v1/cases/case-one', route => route.fulfill({ status: 404, json: {} }));
  await page.goto('/submissions/solution-one');
  await expect(page.getByRole('heading', { name: 'Отправленное решение' })).toBeVisible();
  await expect(page.locator('.submission-meta')).toContainText('На проверке');
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Редактор: solution.sql' })).toContainText(
    'SELECT customer_id FROM orders;',
  );
});
