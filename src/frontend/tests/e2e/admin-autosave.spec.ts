import { expect, Page, test } from '@playwright/test';

const initial = {
  id: 'case-autosave',
  title: 'SQL',
  description: 'Описание',
  expected_result: 'Результат',
  competencies: [{ competency_id: 'sql', required_value: 50, weight: 0.1 }],
  version: 1,
  status: 'draft',
  difficulty: 'review_plus',
  frozen: false,
  created_at: '2026-10-17T10:00:00Z',
};

async function fixture(
  page: Page,
  options: { failCreate?: boolean; createDelay?: number; requiredValue?: number } = {},
) {
  let record = {
    ...initial,
    competencies: [{ ...initial.competencies[0], required_value: options.requiredValue ?? 50 }],
  };
  const creates: { body: Record<string, unknown>; key: string | undefined }[] = [];
  const writes: { kind: string; version: number }[] = [];
  let content = 'SELECT 1;';
  let sha = 'blob-1';
  await page.addInitScript(() =>
    sessionStorage.setItem('prodguild.admin.session.v1', 'organizer-test'),
  );
  await page.route('**/auth/**', route =>
    route.fulfill({ json: { id: 'organizer-test', role: 'user', capabilities: ['organizer'] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/competencies')
      return route.fulfill({
        json: [
          { id: 'sql', name: 'SQL', description: '' },
          { id: 'python', name: 'Python', description: '' },
        ],
      });
    if (url.pathname === '/api/v1/admin/cases' && request.method() === 'POST') {
      const body = request.postDataJSON();
      creates.push({ body, key: request.headers()['idempotency-key'] });
      record = { ...record, ...body };
      if (options.createDelay)
        await new Promise(resolve => setTimeout(resolve, options.createDelay));
      if (options.failCreate && creates.length === 1)
        return route.fulfill({ status: 503, json: {} });
      return route.fulfill({ status: creates.length === 1 ? 201 : 200, json: record });
    }
    if (url.pathname.endsWith('/reference-solution'))
      return route.fulfill({ status: 404, json: {} });
    if (url.pathname.endsWith('/files/content')) {
      if (request.method() === 'PUT') {
        const version = Number(url.searchParams.get('expected_case_version'));
        writes.push({ kind: 'file', version });
        expect(version).toBe(record.version);
        content = request.postDataJSON().content;
        record.version++;
        sha = 'blob-' + record.version;
      }
      return route.fulfill({
        json: { path: 'task.sql', sha, commit_sha: 'head-' + record.version, content },
      });
    }
    if (url.pathname.endsWith('/files'))
      return route.fulfill({
        json: [{ path: 'task.sql', sha, commit_sha: 'head-1', content: null }],
      });
    if (url.pathname.startsWith('/api/v1/admin/cases/')) {
      if (request.method() === 'PATCH') {
        const body = request.postDataJSON();
        writes.push({ kind: 'metadata', version: body.expected_version });
        expect(body.expected_version).toBe(record.version);
        record = { ...record, ...body, version: record.version + 1 };
      }
      return route.fulfill({ json: record });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  return { creates, writes, current: () => record, content: () => content };
}

test('first edit creates one incomplete draft and keeps typing, skills and reload', async ({
  page,
}) => {
  const api = await fixture(page, { createDelay: 300 });
  await page.goto('/admin/new');
  await expect(page.getByRole('button', { name: 'Создать кейс', exact: true })).toHaveCount(0);
  const name = page.getByLabel('Название кейса', { exact: true });
  await name.click();
  await name.pressSequentially('SQL и Python', { delay: 50 });
  await expect(page).toHaveURL(/\/admin\/[^/]+\/edit$/);
  await expect(name).toBeFocused();
  await expect(name).toHaveValue('SQL и Python');
  await page.getByRole('button', { name: 'Компетенции кейса', exact: true }).click();
  await page.getByRole('checkbox', { name: 'SQL', exact: true }).check();
  await page.getByRole('checkbox', { name: 'Python', exact: true }).check();
  await page.getByRole('button', { name: 'Готово', exact: true }).click();
  await expect.poll(() => api.current().competencies.length).toBe(2);
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено');
  expect(api.creates).toHaveLength(1);
  expect(api.creates[0].body).toMatchObject({
    title: '',
    description: '',
    expected_result: '',
    competencies: [],
    status: 'draft',
  });
  await page.reload();
  await expect(name).toHaveValue('SQL и Python');
  await expect(page.getByRole('button', { name: 'Убрать навык Python' })).toBeVisible();
  expect(api.creates).toHaveLength(1);
  await expect(
    page.getByText(/Для публикации заполните: описание, ожидаемый результат/),
  ).toBeVisible();
});

test('unknown create response survives reload with the same id and exact request key', async ({
  page,
}) => {
  const api = await fixture(page, { failCreate: true });
  page.on('dialog', dialog => dialog.accept());
  await page.goto('/admin/new');
  await page.getByLabel('Название кейса', { exact: true }).fill('Первый черновик');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/new\?draft=/);
  await page.reload();
  await expect(page).toHaveURL(/\/admin\/[^/]+\/edit$/);
  await expect.poll(() => api.current().title).toBe('Первый черновик');
  expect(api.creates).toHaveLength(2);
  expect(api.creates[1]).toEqual(api.creates[0]);
});

test('metadata and material autosaves share current case versions', async ({ page }) => {
  const api = await fixture(page);
  await page.goto('/admin/case-autosave/edit');
  await page.getByLabel('Название кейса', { exact: true }).fill('Новый SQL');
  await page.getByRole('link', { name: 'Материалы', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 2;');
  await expect.poll(() => api.content()).toBe('SELECT 2;');
  await expect.poll(() => api.current().title).toBe('Новый SQL');
  expect(api.writes.map(write => write.version)).toEqual([1, 2]);
  await expect(page.getByRole('button', { name: 'Обновить файлы' })).toHaveCount(0);
  await expect(
    page.locator('.embedded-explorer').getByRole('button', { name: 'Добавить файл' }),
  ).toBeVisible();
  await page.reload();
  await expect(editor).toHaveText('SELECT 2;');
});

test('publication drains metadata edited while a material write is in flight', async ({ page }) => {
  const api = await fixture(page);
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => (release = resolve));
  let started: () => void = () => {};
  const writing = new Promise<void>(resolve => (started = resolve));
  await page.route('**/reference-solution', route =>
    route.fulfill({ json: { version: 1, head_commit_sha: 'reference-1', ready: true } }),
  );
  await page.route('**/files/content?*', async route => {
    if (route.request().method() === 'PUT') {
      started();
      await pending;
    }
    return route.fallback();
  });
  await page.goto('/admin/case-autosave/edit?section=materials');
  await page.getByRole('button', { name: 'Опубликовать кейс', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await page.keyboard.insertText('SELECT 99;');
  await page.getByRole('button', { name: 'Подтвердить публикацию', exact: true }).click();
  await writing;
  await page.getByRole('link', { name: 'Описание', exact: true }).click();
  await page.getByLabel('Название кейса', { exact: true }).fill('Название перед публикацией');
  release();
  await expect(page.getByText('Кейс опубликован', { exact: true })).toBeVisible();
  expect(api.current().title).toBe('Название перед публикацией');
  expect(api.writes.map(write => write.kind)).toEqual(['file', 'metadata', 'metadata']);
  expect(api.writes.map(write => write.version)).toEqual([1, 2, 3]);
});

test('invalid numeric input keeps its local value while other metadata autosaves', async ({
  page,
}) => {
  const api = await fixture(page);
  page.on('dialog', dialog => dialog.accept());
  await page.goto('/admin/case-autosave/edit');
  await page.getByLabel('Вес навыка SQL, %', { exact: true }).fill('');
  await page.getByLabel('Описание', { exact: true }).fill('Описание с незавершённым весом');
  await expect.poll(() => api.current().description).toBe('Описание с незавершённым весом');
  expect(api.current().competencies[0].weight).toBe(0.1);
  await expect(page.locator('.save-status').first()).toContainText('Проверьте настройки навыков');
  await page.reload();
  await expect(page.getByLabel('Вес навыка SQL, %', { exact: true })).toHaveValue('');
  await page.getByLabel('Вес навыка SQL, %', { exact: true }).fill('20');
  await expect.poll(() => api.current().competencies[0].weight).toBe(0.2);
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено');
});

test('named difficulty preserves an existing requirement until explicitly changed', async ({
  page,
}, info) => {
  const api = await fixture(page, { requiredValue: 34 });
  await page.goto('/admin/case-autosave/edit');
  const difficulty = page.getByRole('combobox', { name: 'Сложность по навыку SQL', exact: true });
  const weight = page.getByRole('textbox', { name: 'Вес навыка SQL, %', exact: true });
  await expect(difficulty).toHaveValue('Средний');
  await expect(weight).toHaveValue('10');
  await expect(page.getByText(/до 12 XP/)).toBeVisible();
  expect(api.writes).toEqual([]);
  await page.getByLabel('Описание', { exact: true }).fill('Сохраняем старое требование');
  await expect.poll(() => api.current().description).toBe('Сохраняем старое требование');
  expect(api.current().competencies[0].required_value).toBe(34);
  await difficulty.click();
  await expect(page.getByRole('listbox').getByRole('option')).toHaveText([
    'Лёгкий',
    'Средний',
    'Сложный',
    'Экспертный',
  ]);
  await page.getByRole('option', { name: 'Сложный', exact: true }).click();
  await expect.poll(() => api.current().competencies[0].required_value).toBe(50);
  await page.screenshot({
    path: info.outputPath('admin-skill-settings-default.png'),
    fullPage: true,
  });
  await weight.fill('12,5');
  await expect.poll(() => api.current().competencies[0].weight).toBe(0.125);
  await expect(weight).toHaveValue('12,5');
  await expect(page.getByText(/до 15 XP/)).toBeVisible();
  await page.reload();
  await expect(difficulty).toHaveValue('Сложный');
  await expect(weight).toHaveValue('12,5');
  await page.screenshot({ path: info.outputPath('admin-skill-settings.png'), fullPage: true });
  for (const [label, value] of [
    ['Лёгкий', 0],
    ['Средний', 25],
    ['Экспертный', 75],
  ] as const) {
    await difficulty.click();
    await page.getByRole('option', { name: label, exact: true }).click();
    await expect.poll(() => api.current().competencies[0].required_value).toBe(value);
  }
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
});

test('percentage boundaries report the field and preserve invalid local input', async ({
  page,
}) => {
  const api = await fixture(page);
  page.on('dialog', dialog => dialog.accept());
  await page.goto('/admin/case-autosave/edit');
  const weight = page.getByRole('textbox', { name: 'Вес навыка SQL, %', exact: true });
  for (const value of ['', '0', '-1', '25,1', 'не число']) {
    await weight.fill(value);
    await expect(weight).toHaveAttribute('aria-invalid', 'true');
    await expect(
      page.getByText(
        'Введите вес больше 0 и не выше 25%. Например, 10. Это значение пока не сохранено.',
        { exact: true },
      ),
    ).toBeVisible();
    await expect(weight).toHaveValue(value);
    expect(api.current().competencies[0].weight).toBe(0.1);
  }
  await weight.fill('25');
  await expect.poll(() => api.current().competencies[0].weight).toBe(0.25);
  await expect(weight).toHaveAttribute('aria-invalid', 'false');
  await expect(page.getByText(/до 30 XP/)).toBeVisible();
  const difficulty = page.getByRole('combobox', { name: 'Сложность по навыку SQL', exact: true });
  await difficulty.focus();
  await difficulty.press('Backspace');
  await expect(difficulty).toHaveAttribute('aria-invalid', 'true');
  await expect(
    page.getByText('Выберите один из четырёх уровней сложности.', { exact: true }),
  ).toBeVisible();
  await page.getByLabel('Описание', { exact: true }).fill('Сложность ещё не выбрана');
  await expect.poll(() => api.current().description).toBe('Сложность ещё не выбрана');
  expect(api.current().competencies[0].required_value).toBe(50);
  await page.reload();
  await expect(difficulty).toHaveValue('Выберите сложность');
  await difficulty.click();
  await page.getByRole('option', { name: 'Средний', exact: true }).click();
  await expect.poll(() => api.current().competencies[0].required_value).toBe(25);
});
