import { expect, Page, test } from '@playwright/test';

const caseRecord = {
  id: 'case-one',
  title: 'Поиск по каталогу',
  description: 'Найдите и исправьте ошибку в поиске.',
  expected_result: 'Работающий поиск',
  difficulty: 'review_plus',
  created_at: '2026-10-17T10:00:00Z',
  version: 1,
  frozen: false,
  status: 'draft',
  competencies: [{ competency_id: 'sql', required_value: 50, weight: 0.1 }],
};
const fileRecord = {
  path: 'task.sql',
  sha: 'file-v1',
  commit_sha: 'head-v1',
  content: 'SELECT 1;',
};

async function fixture(page: Page): Promise<void> {
  await page.addInitScript(() => {
    sessionStorage.setItem('prodguild.admin.session.v1', 'organizer-test');
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'organizer-test', role: 'user', capabilities: ['organizer'] } }),
  );
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/competencies')
      return route.fulfill({ json: [{ id: 'sql', name: 'SQL', description: 'Запросы к данным' }] });
    if (url.pathname === '/api/v1/admin/cases') return route.fulfill({ json: [caseRecord] });
    if (url.pathname === '/api/v1/admin/cases/case-one') return route.fulfill({ json: caseRecord });
    if (url.pathname.endsWith('/reference-solution'))
      return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
    if (url.pathname.endsWith('/files')) return route.fulfill({ json: [fileRecord] });
    if (url.pathname.endsWith('/files/content')) return route.fulfill({ json: fileRecord });
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
}

async function layout(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
}

async function fillMetadata(page: Page): Promise<void> {
  await page.getByLabel('Название кейса', { exact: true }).fill('SQL для команды');
  await page.getByLabel('Описание', { exact: true }).fill('Исправьте запрос к каталогу.');
  await page.getByLabel('Ожидаемый результат', { exact: true }).fill('Точный результат запроса.');
  await page.getByRole('button', { name: 'Компетенции кейса', exact: true }).click();
  await page.getByRole('checkbox', { name: 'SQL', exact: true }).check();
  await page.getByRole('button', { name: 'Готово', exact: true }).click();
}

test.beforeEach(async ({ page }) => fixture(page));

test('case links open separate pages and keep the selected workspace after reload', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/admin');
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toHaveAttribute(
    'href',
    '/admin/new',
  );
  await expect(page.getByText('Здесь будет ваш кейс', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Выберите кейс из списка или создайте новый.')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Обновить список', exact: true })).toHaveCount(0);
  const row = page.locator('.case-row');
  await expect(row).toHaveAttribute('href', '/admin/case-one/edit');
  await row.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/admin\/case-one\/edit$/);
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue(caseRecord.title);
  await page.getByRole('link', { name: 'Материалы', exact: true }).click();
  await expect(page).toHaveURL(/\?section=materials$/);
  await expect(page.locator('app-file-workspace:visible app-remote-files')).toHaveCount(1);
  await expect(page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true })).toHaveText(
    'SELECT 1;',
  );
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true })).toHaveText(
    'SELECT 1;',
  );
  await layout(page);
  await page.screenshot({ path: info.outputPath('admin-materials.png'), fullPage: true });
  await page.getByRole('link', { name: 'Эталонное решение', exact: true }).click();
  await expect(page).toHaveURL(/\?section=reference$/);
  await expect(page.getByRole('button', { name: 'Создать эталон', exact: true })).toBeEnabled();
  await expect(page.locator('app-remote-files:visible')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Создать эталон', exact: true })).toBeEnabled();
  await layout(page);
  await page.screenshot({ path: info.outputPath('admin-reference-empty.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('lookahead pagination displays twenty rows without an empty final page', async ({
  page,
}, info) => {
  const requests: { limit: number; offset: number; status: string | null }[] = [];
  await page.route('**/api/v1/admin/cases?*', route => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset'));
    const limit = Number(url.searchParams.get('limit'));
    requests.push({ limit, offset, status: url.searchParams.get('status') });
    return route.fulfill({
      json: Array.from({ length: offset ? 20 : 21 }, (_, index) => ({
        ...caseRecord,
        id: `case-${offset + index + 1}`,
        title: `Кейс ${offset + index + 1}`,
      })),
    });
  });
  await page.goto('/admin');
  const rows = page.locator('.case-row');
  await expect(rows).toHaveCount(20);
  await expect(rows.last()).toContainText('Кейс 20');
  const next = page.getByRole('button', { name: 'Следующая страница кейсов', exact: true });
  const previous = page.getByRole('button', { name: 'Предыдущая страница кейсов', exact: true });
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(rows.first()).toContainText('Кейс 21');
  await expect(rows).toHaveCount(20);
  await expect(next).toBeDisabled();
  await expect(previous).toBeEnabled();
  await layout(page);
  await page.screenshot({ path: info.outputPath('admin-pagination.png'), fullPage: true });
  await previous.click();
  await expect(rows.first()).toContainText('Кейс 1');
  expect(requests).toEqual([
    { limit: 21, offset: 0, status: null },
    { limit: 21, offset: 20, status: null },
    { limit: 21, offset: 0, status: null },
  ]);
});

test('failed page request retains rows and retries the requested page', async ({ page }) => {
  let nextAttempts = 0;
  await page.route('**/api/v1/admin/cases?*', route => {
    const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    if (!offset)
      return route.fulfill({
        json: Array.from({ length: 21 }, (_, index) => ({
          ...caseRecord,
          id: `first-${index}`,
          title: `Первый набор ${index + 1}`,
        })),
      });
    if (++nextAttempts === 1) return route.fulfill({ status: 503, json: {} });
    return route.fulfill({ json: [{ ...caseRecord, title: 'Последний кейс' }] });
  });
  await page.goto('/admin');
  await expect(page.locator('.case-row')).toHaveCount(20);
  await page.getByRole('button', { name: 'Следующая страница кейсов', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('.case-row')).toHaveCount(20);
  await expect(page.getByText('Страница 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(page.locator('.case-row')).toHaveCount(1);
  await expect(page.locator('.case-row')).toContainText('Последний кейс');
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(nextAttempts).toBe(2);
});

test('status selection requests the server filter and resets pagination', async ({
  page,
}, info) => {
  const queries: URLSearchParams[] = [];
  await page.route('**/api/v1/admin/cases?*', route => {
    const query = new URL(route.request().url()).searchParams;
    queries.push(query);
    if (query.get('status') === 'published')
      return route.fulfill({ json: [{ ...caseRecord, status: 'published' }] });
    return route.fulfill({
      json: Array.from({ length: 21 }, (_, index) => ({
        ...caseRecord,
        id: `case-${index}`,
        title: `Кейс ${index + 1}`,
      })),
    });
  });
  await page.goto('/admin');
  const status = page.getByRole('combobox', { name: 'Статус', exact: true });
  await expect(status).toHaveValue('Все статусы');
  await expect(status).toHaveCSS('box-shadow', 'none');
  await expect(page.getByRole('searchbox')).toHaveCSS('box-shadow', 'none');
  for (const field of await page.locator('.list-toolbar tui-textfield').all()) {
    await expect(field).toHaveCSS('box-shadow', 'none');
    expect(
      await field.evaluate(element =>
        ['::before', '::after'].map(pseudo => getComputedStyle(element, pseudo).boxShadow),
      ),
    ).toEqual(['none', 'none']);
  }
  await page.screenshot({
    path: info.outputPath('admin-status-default.png'),
    animations: 'disabled',
  });
  await status.focus();
  await status.press('ArrowDown');
  await expect(page.getByRole('option', { name: 'Опубликованные', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('option', { name: 'Опубликованные', exact: true })).toHaveCount(0);
  await expect(status).toHaveValue('Все статусы');
  await page.getByRole('button', { name: 'Следующая страница кейсов', exact: true }).click();
  await status.click();
  await expect(page.getByRole('option', { name: 'Опубликованные', exact: true })).toBeVisible();
  await layout(page);
  await page.screenshot({ path: info.outputPath('admin-status-menu.png'), animations: 'disabled' });
  await page.getByRole('option', { name: 'Опубликованные', exact: true }).click();
  await expect(page.locator('.case-row')).toHaveCount(1);
  await expect(page.locator('.case-row')).toContainText('Опубликован');
  await expect(status).toHaveValue('Опубликованные');
  await expect(page.getByRole('navigation', { name: 'Страницы кейсов' })).toHaveCount(0);
  expect(queries.at(-1)?.get('status')).toBe('published');
  expect(queries.at(-1)?.get('offset')).toBe('0');
  await expect(page.getByRole('listbox')).toHaveCount(0);
  await status.focus();
  await status.press('ArrowDown');
  const allStatuses = page.getByRole('option', { name: 'Все статусы', exact: true });
  await allStatuses.focus();
  await allStatuses.press('Enter');
  await expect(status).toHaveValue('Все статусы');
  await expect(page.locator('.case-row')).toHaveCount(20);
  expect(queries.at(-1)?.get('status')).toBeNull();
});

test('failed automatic creation keeps editable metadata and retries the original draft', async ({
  page,
}, info) => {
  const creates: { key: string | undefined; body: unknown }[] = [];
  let saved = { ...caseRecord };
  await page.route('**/api/v1/admin/cases', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON();
    creates.push({ key: route.request().headers()['idempotency-key'], body });
    expect(body).toMatchObject({
      title: '',
      description: '',
      expected_result: '',
      competencies: [],
      status: 'draft',
    });
    if (creates.length === 1) return route.fulfill({ status: 503, json: {} });
    saved = { ...caseRecord, ...body, id: caseRecord.id };
    return route.fulfill({ status: 201, json: saved });
  });
  await page.route('**/api/v1/admin/cases/case-one', route => {
    if (route.request().method() === 'PATCH')
      saved = { ...saved, ...route.request().postDataJSON(), version: saved.version + 1 };
    return route.fulfill({ json: saved });
  });
  await page.goto('/admin/new');
  await expect(page.getByRole('button', { name: 'Создать кейс', exact: true })).toHaveCount(0);
  await fillMetadata(page);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue('SQL для команды');
  await expect(page.getByLabel('Описание', { exact: true })).toHaveValue(
    'Исправьте запрос к каталогу.',
  );
  await layout(page);
  await page.screenshot({ path: info.outputPath('admin-create-error.png'), fullPage: true });
  await expect(page.getByLabel('Название кейса', { exact: true })).toBeEditable();
  await page.getByRole('button', { name: 'Повторить операцию', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/case-one\/edit$/);
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено');
  expect(creates).toHaveLength(2);
  expect(creates[1]).toEqual(creates[0]);
  expect(saved).toMatchObject({ title: 'SQL для команды', competencies: caseRecord.competencies });
  await page.getByRole('link', { name: 'Материалы', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Редактор: task.sql' })).toHaveText('SELECT 1;');
});

test('version conflict preserves metadata after automatic save and reload', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/api/v1/admin/cases/case-one', route =>
    route.request().method() === 'PATCH'
      ? route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } })
      : route.fulfill({ json: caseRecord }),
  );
  await page.goto('/admin/case-one/edit');
  await page.getByLabel('Название кейса', { exact: true }).fill('Обновлённый поиск');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue('Обновлённый поиск');
  await page.reload();
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue('Обновлённый поиск');
  await expect(page.getByRole('button', { name: 'Сохранить описание', exact: true })).toHaveCount(
    0,
  );
});

test('unsaved file survives automatic save failure and reload', async ({ page }) => {
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/api/v1/admin/cases/case-one/files/content?*', route =>
    route.request().method() === 'PUT'
      ? route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } })
      : route.fulfill({ json: fileRecord }),
  );
  await page.goto('/admin/case-one/edit?section=materials');
  const editor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42 AS answer;');
  await expect(editor).toHaveText('SELECT 42 AS answer;');
  await expect(page.locator('app-remote-files:visible').getByRole('alert')).toBeVisible();
  await expect(editor).toHaveText('SELECT 42 AS answer;');
  await page.reload();
  await expect(editor).toHaveText('SELECT 42 AS answer;');
  await expect(page.locator('app-remote-files:visible').getByRole('status')).toHaveText(
    'Не удалось сохранить',
  );
  await expect(page.getByRole('button', { name: 'Скачать все черновики' })).toBeEnabled();
});

test('frozen cases keep materials and reference read-only', async ({ page }, info) => {
  await page.route('**/api/v1/admin/cases/case-one', route =>
    route.fulfill({ json: { ...caseRecord, status: 'published', frozen: true } }),
  );
  await page.route('**/api/v1/admin/cases/case-one/reference-solution', route =>
    route.fulfill({ json: { version: 2, head_commit_sha: 'reference-v2', ready: true } }),
  );
  for (const section of ['materials', 'reference']) {
    await page.goto(`/admin/case-one/edit?section=${section}`);
    const editor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
    await expect(editor).toHaveText('SELECT 1;');
    await expect(editor).not.toBeEditable();
    await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Добавить файл', exact: true })).toHaveCount(0);
    await layout(page);
  }
  await page.getByRole('link', { name: 'Описание', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Сохранить описание', exact: true })).toHaveCount(
    0,
  );
  await page.screenshot({ path: info.outputPath('admin-frozen.png'), fullPage: true });
});

test('uncertain automatic file save retains its exact operation for retry', async ({ page }) => {
  let writes = 0;
  let key = '';
  let body = '';
  let saved = fileRecord;
  await page.route('**/api/v1/admin/cases/case-one/files', route =>
    route.fulfill({ json: [saved] }),
  );
  await page.route('**/api/v1/admin/cases/case-one/files/content?*', route => {
    if (route.request().method() !== 'PUT') return route.fulfill({ json: saved });
    writes++;
    if (writes === 1) {
      key = route.request().headers()['idempotency-key'];
      body = route.request().postData() ?? '';
      return route.fulfill({ status: 503, json: { code: 'DEPENDENCY_UNAVAILABLE' } });
    }
    expect(route.request().headers()['idempotency-key']).toBe(key);
    expect(route.request().postData()).toBe(body);
    saved = { ...fileRecord, sha: 'file-v2', content: 'SELECT 42 AS answer;' };
    return route.fulfill({ json: saved });
  });
  await page.goto('/admin/case-one/edit?section=materials');
  const files = page.getByRole('region', { name: 'Материалы кейса', exact: true });
  const editor = files.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42 AS answer;');
  await expect(files.getByRole('alert')).toBeVisible();
  await expect(editor).toHaveText('SELECT 42 AS answer;');
  await expect(editor).toBeEditable();
  await files.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(files.getByRole('status')).toHaveText('Сохранено');
  await expect(editor).toBeEditable();
  expect(writes).toBe(2);
  await page.reload();
  await expect(editor).toHaveText('SELECT 42 AS answer;');
});

test('leaving during file creation warns and late completion does not reopen the case', async ({
  page,
}) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  let requested: () => void = () => {};
  const requestStarted = new Promise<void>(resolve => {
    requested = resolve;
  });
  let completed: () => void = () => {};
  const requestCompleted = new Promise<void>(resolve => {
    completed = resolve;
  });
  await page.route('**/api/v1/admin/cases/case-one/files?*', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    requested();
    await pending;
    await route.fulfill({ status: 201, json: { ...fileRecord, path: 'notes.md', content: '' } });
    completed();
  });
  await page.goto('/admin/case-one/edit?section=materials');
  const files = page.getByRole('region', { name: 'Материалы кейса', exact: true });
  await files.getByRole('button', { name: 'Добавить файл', exact: true }).click();
  const filename = files.getByLabel('Путь нового файла', { exact: true });
  await expect(filename).toBeFocused();
  await filename.pressSequentially('notes.md');
  await filename.press('Enter');
  await requestStarted;
  page.once('dialog', async dialog => {
    expect(dialog.message()).toContain('незавершённая операция');
    await dialog.dismiss();
  });
  await page.getByRole('link', { name: 'Все кейсы', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/case-one\/edit\?section=materials$/);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: 'Все кейсы', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  release();
  await requestCompleted;
  await expect(page.locator('.case-row')).toHaveCount(1);
  await expect(page).toHaveURL(/\/admin$/);
});
