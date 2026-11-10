import { expect, Page, test } from '@playwright/test';
import { emptyAttachments } from './fixtures/attachments';
import type { Draft } from '../../src/app/api/review.service';

async function setup(page: Page, options: { conflict?: boolean; empty?: boolean } = {}) {
  let draft: Draft = {
    version: 1,
    score: null,
    verdict: null,
    feedback: { strengths: '', problems: '', next_steps: '' },
    comments: [],
  };
  const requests: string[] = [];
  const task = {
    id: 'line-review',
    submission_id: 'submission-one',
    kind: 'review',
    review_id: null,
    revision_id: null,
    status: 'assigned',
    lease_expires_at: '2030-10-18T10:00:00Z',
  };
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({
        token: 'fixture-only',
        email: 'review@example.com',
        name: 'Ревьюер',
      }),
    ),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'reviewer', role: 'user', capabilities: ['participant'] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('attachments'))
      return route.fulfill({ json: emptyAttachments(draft.version) });
    if (path.endsWith('/review-mode'))
      return route.fulfill({ json: { enabled: true, status: 'available', can_review: true } });
    if (path.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (path.endsWith('/workspace'))
      return route.fulfill({
        json: {
          assignment: task,
          draft,
          case: {
            title: 'Аналитика клиентских подписок',
            description: 'Рассчитайте выручку и сравните результаты по месяцам.',
            expected_result: 'SQL-запрос и пояснение выбранного подхода.',
          },
        },
      });
    if (path.endsWith('/draft')) {
      if (route.request().method() === 'PUT') {
        if (options.conflict)
          return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
        const body = route.request().postDataJSON();
        expect(body.expected_version).toBe(draft.version);
        draft = {
          score: body.score,
          verdict: body.verdict,
          feedback: body.feedback,
          comments: body.comments,
          version: draft.version + 1,
        };
      }
      return route.fulfill({ json: draft });
    }
    if (path.endsWith('/diff'))
      return route.fulfill({
        json: {
          entries: options.empty
            ? []
            : url.searchParams.get('offset') === '20'
              ? [{ comment_path: 'docs/notes.md', status: 'added', additions: 1, deletions: 0 }]
              : [
                  {
                    comment_path: 'src/revenue.sql',
                    status: 'modified',
                    additions: 2,
                    deletions: 1,
                  },
                  { comment_path: 'legacy.sql', status: 'deleted', additions: 0, deletions: 1 },
                ],
          next_offset: !options.empty && url.searchParams.get('offset') !== '20' ? 20 : null,
        },
      });
    if (path.endsWith('/diff/lines')) {
      requests.push(url.search);
      const file = url.searchParams.get('path');
      const next = url.searchParams.get('offset') === '137';
      return route.fulfill({
        json: {
          lines:
            file === 'legacy.sql'
              ? [
                  {
                    kind: 'deletion',
                    old_line: 1,
                    new_line: null,
                    text: 'DROP TABLE subscriptions;',
                    text_truncated: false,
                  },
                ]
              : file === 'docs/notes.md'
                ? [
                    {
                      kind: 'addition',
                      old_line: null,
                      new_line: 1,
                      text: '# Результаты анализа',
                      text_truncated: false,
                    },
                  ]
                : next
                  ? [
                      {
                        kind: 'addition',
                        old_line: null,
                        new_line: 138,
                        text: 'GROUP BY month;',
                        text_truncated: true,
                      },
                    ]
                  : [
                      {
                        kind: 'context',
                        old_line: 1,
                        new_line: 1,
                        text: '-- Monthly revenue',
                        text_truncated: false,
                      },
                      {
                        kind: 'deletion',
                        old_line: 2,
                        new_line: null,
                        text: 'SELECT SUM(amount) FROM subscriptions;',
                        text_truncated: false,
                      },
                      {
                        kind: 'addition',
                        old_line: null,
                        new_line: 2,
                        text: "SELECT date_trunc('month', created_at) AS month, SUM(amount)",
                        text_truncated: false,
                      },
                      {
                        kind: 'addition',
                        old_line: null,
                        new_line: 3,
                        text: 'FROM subscriptions; ' + ' '.repeat(100) + '-- длинная строка',
                        text_truncated: false,
                      },
                    ],
          next_offset: file === 'src/revenue.sql' && !next ? 137 : null,
        },
      });
    }
    if (path.endsWith('/files'))
      return route.fulfill({
        json: {
          entries: [
            { path: 'src/revenue.sql', sha: 'sha' },
            { path: 'unchanged.sql', sha: 'unchanged' },
          ],
          next_offset: null,
        },
      });
    if (path.endsWith('/files/content'))
      return route.fulfill({
        json: {
          path: url.searchParams.get('path') ?? 'src/revenue.sql',
          sha: 'sha',
          commit_sha: 'snapshot-head',
          content:
            url.searchParams.get('path') === 'unchanged.sql'
              ? 'SELECT 1;\nSELECT 2;\nSELECT 3;\n'
              : 'SELECT 2;',
        },
      });
    if (path.endsWith('/line-review')) return route.fulfill({ json: task });
    return route.fulfill({ json: [] });
  });
  await page.goto('/reviews/line-review');
  await page.getByRole('button', { name: '2. Файлы решения' }).click();
  return { draft: () => draft, requests };
}

test('anchors old/new lines, keeps unfinished text when switching files, saves and restores exact comments', async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await setup(page);
  await page.getByRole('button', { name: 'Комментарий к строке 2, решение', exact: true }).click();
  const body = page.getByLabel('Комментарий', { exact: true });
  await expect(body).toBeFocused();
  await body.pressSequentially('Добавьте группировку по месяцам.');
  await page.getByRole('button', { name: /legacy.sql.*Удалён/ }).click();
  await expect(body).toHaveValue('Добавьте группировку по месяцам.');
  await expect(page.locator('.comment-anchor')).toContainText('src/revenue.sql');
  await page.getByRole('button', { name: 'Комментарий к строке 1, исходник', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Сначала добавьте текущий комментарий');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await page.getByRole('button', { name: 'Комментарий к строке 1, исходник', exact: true }).click();
  await body.fill('Удаление старого запроса обосновано.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(
    page.getByRole('status').filter({ hasText: 'Черновик сохранён на сервере' }),
  ).toBeVisible();
  expect(state.draft().comments).toEqual([
    { body: 'Добавьте группировку по месяцам.', path: 'src/revenue.sql', line: 2, side: 'new' },
    { body: 'Удаление старого запроса обосновано.', path: 'legacy.sql', line: 1, side: 'old' },
  ]);
  await page.reload();
  await page.getByRole('button', { name: '2. Файлы решения' }).click();
  await expect(page.getByRole('article', { name: 'Комментарий к строке' })).toContainText(
    'Добавьте группировку',
  );
  await page.getByLabel('Просмотрено', { exact: true }).check();
  await expect(page.getByText('1 просмотрено')).toBeVisible();
  await page.locator('app-review-diff').scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath('review-workspace.png'),
    fullPage: true,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('follows server line cursors, supports deleted files and file pagination', async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Следующие строки', exact: true }).click();
  await expect(page.getByText(/GROUP BY month/)).toContainText('строка сокращена сервером');
  expect(state.requests.at(-1)).toContain('offset=137');
  await page.getByRole('button', { name: 'Предыдущие строки', exact: true }).click();
  await expect(page.getByText('-- Monthly revenue', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /legacy.sql.*Удалён/ }).click();
  await expect(page.getByRole('button', { name: /Комментарий.*решение/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Следующие файлы', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'docs/notes.md', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Предыдущие файлы', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'src/revenue.sql', exact: true })).toBeVisible();
});

test('preserves inline comments on save conflict and blocks send with an unfinished comment', async ({
  page,
}) => {
  await setup(page, { conflict: true });
  await page.getByRole('button', { name: 'Комментарий к строке 2, исходник', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page
    .getByLabel('Комментарий', { exact: true })
    .fill('Проверьте обработку пустых значений.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('другой вкладке');
  await expect(page.getByLabel('Комментарий', { exact: true })).toHaveValue(
    'Проверьте обработку пустых значений.',
  );
  await page.getByLabel('Комментарий', { exact: true }).fill('Ещё не готовый комментарий');
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await expect(
    page.getByText('Остался не добавленный комментарий', { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Подтвердить отправку ревью' })).toHaveCount(0);
});

test('empty diff has an honest fallback to immutable full files', async ({ page }) => {
  await setup(page, { empty: true });
  await expect(page.getByRole('heading', { name: 'Нет изменений на этой странице' })).toBeVisible();
  await page.getByRole('button', { name: 'Все файлы', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Файлы решения' })).toBeVisible();
});

test('expanded review keeps file views, comments, save feedback and navigation usable', async ({
  page,
}, info) => {
  const options = { conflict: false };
  const state = await setup(page, options);
  await page.getByRole('button', { name: /legacy.sql.*Удалён/ }).click();
  await page.getByLabel('Просмотрено', { exact: true }).check();
  const diff = page.getByRole('region', { name: 'Различия по строкам', exact: true });
  await diff.evaluate(element => element.setAttribute('data-diff-identity', 'original'));
  await page.getByRole('button', { name: 'Развернуть код', exact: true }).click();
  const target = page.locator('[data-code-maximize-target]');
  await expect(target).toBeVisible();
  await target.evaluate(async element => {
    await Promise.all(
      element.getAnimations().map(animation => animation.finished.catch(() => undefined)),
    );
  });
  await expect(page.locator('h1')).toBeHidden();
  await expect(target.getByRole('complementary', { name: 'Обсуждения к файлу' })).toBeVisible();
  const viewport = page.viewportSize();
  const bounds = await target.boundingBox();
  if (!viewport || !bounds) throw new Error('Expanded review has a viewport and bounds');
  expect(bounds.y).toBe(0);
  expect(bounds.height).toBe(viewport.height);
  expect(Math.round(bounds.x + bounds.width)).toBe(viewport.width);
  await expect(page.locator('app-sidebar')).toBeHidden();
  await expect(diff).toHaveAttribute('data-diff-identity', 'original');
  await expect(page.getByRole('heading', { name: 'legacy.sql', exact: true })).toBeVisible();
  await expect(page.getByLabel('Просмотрено', { exact: true })).toBeChecked();
  await page.screenshot({ path: info.outputPath('review-diff-expanded.png') });
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).focus();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Развернуть код', exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Развернуть код', exact: true }).click();
  await page.getByRole('button', { name: 'Комментарий к строке 1, исходник', exact: true }).click();
  await expect(target).toBeVisible();
  const comment = page.getByLabel('Комментарий', { exact: true });
  await expect(comment).toBeFocused();
  await expect(page.locator('.comment-anchor')).toContainText('legacy.sql');
  await comment.fill('Проверьте удаление таблицы.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(page.getByRole('article', { name: 'Комментарий к строке' })).toContainText(
    'Проверьте удаление таблицы.',
  );
  await target.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(
    target.getByRole('status').filter({ hasText: 'Черновик сохранён на сервере' }),
  ).toBeVisible();
  expect(state.draft().comments).toEqual([
    { body: 'Проверьте удаление таблицы.', path: 'legacy.sql', line: 1, side: 'old' },
  ]);
  await target.getByRole('button', { name: 'Все файлы', exact: true }).click();
  await target
    .getByRole('navigation', { name: 'Файлы решения', exact: true })
    .getByRole('button', { name: 'src/revenue.sql', exact: true })
    .click();
  const code = target.getByRole('textbox', { name: 'Редактор: src/revenue.sql', exact: true });
  await expect(code).toHaveText('SELECT 2;');
  await code.evaluate(element => element.setAttribute('data-editor-identity', 'original'));
  await target.getByRole('button', { name: 'Изменения', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'legacy.sql', exact: true })).toBeVisible();
  await target.getByRole('button', { name: 'Все файлы', exact: true }).click();
  await expect(code).toHaveAttribute('data-editor-identity', 'original');
  await target.getByRole('button', { name: 'Изменения', exact: true }).click();
  await target
    .getByRole('article', { name: 'Комментарий к строке' })
    .getByRole('button', { name: 'Удалить комментарий 1', exact: true })
    .click();
  options.conflict = true;
  await target.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(target.getByRole('alert')).toContainText('другой вкладке');
  await expect(target.getByRole('alert')).toBeFocused();
  await expect(target.getByText('Черновик сохранён', { exact: true })).toBeVisible();
  await expect(target.getByRole('button', { name: 'Свернуть код', exact: true })).toBeVisible();
  await expect(diff).toHaveAttribute('data-diff-identity', 'original');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('selects an immutable unchanged file range and persists it with an incomplete review', async ({
  page,
}, info) => {
  const state = await setup(page);
  await page.getByRole('button', { name: 'Все файлы', exact: true }).click();
  await page
    .getByRole('navigation', { name: 'Файлы решения', exact: true })
    .getByRole('button', { name: 'unchanged.sql', exact: true })
    .click();
  const editor = page.locator('app-snapshot-viewer .cm-editor');
  const gutters = editor.locator('.cm-lineNumbers .cm-gutterElement');
  await gutters.filter({ hasText: /^1$/ }).click();
  await gutters.filter({ hasText: /^3$/ }).click({ modifiers: ['Shift'] });
  await page.getByRole('button', { name: 'Комментировать выделение', exact: true }).click();
  await expect(page.locator('.comment-anchor')).toContainText('1–3');
  await page.getByLabel('Комментарий', { exact: true }).fill('Можно объединить запросы.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(page.getByLabel('Комментарий', { exact: true })).toBeEmpty();
  expect(state.draft().score).toBeNull();
  expect(state.draft().feedback).toEqual({ strengths: '', problems: '', next_steps: '' });
  expect(state.draft().comments).toEqual([
    { body: 'Можно объединить запросы.', path: 'unchanged.sql', side: 'new', line: 1, end_line: 3 },
  ]);
  let releaseFiles!: () => void;
  const filesReady = new Promise<void>(resolve => {
    releaseFiles = resolve;
  });
  await page.route('**/api/v1/review-assignments/line-review/files?*', async route => {
    await filesReady;
    await route.fulfill({
      json: {
        entries: [{ path: 'src/revenue.sql', sha: 'sha' }],
        next_offset: 20,
      },
    });
  });
  await page.reload();
  await page.getByRole('button', { name: '2. Файлы решения' }).click();
  await page.getByRole('button', { name: /unchanged.sql · 1–3/ }).click();
  releaseFiles();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: unchanged.sql', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Все файлы', exact: true })).toHaveAttribute(
    'data-appearance',
    'accent',
  );
  await expect(page.getByRole('button', { name: 'Изменения', exact: true })).toHaveAttribute(
    'data-appearance',
    'secondary',
  );
  await expect(editor.locator('.cm-selectionBackground').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('review-range.png'), fullPage: true });
});
