import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';

const initial = {
  id: 'conflict-case',
  title: 'Запросы к данным',
  description: 'Исправьте SQL-запрос.',
  expected_result: 'Корректный результат.',
  created_at: '2026-10-17T10:00:00Z',
  difficulty: 'review_plus',
  version: 1,
  status: 'draft',
  frozen: false,
  competencies: [{ competency_id: 'sql', required_value: 50, weight: 0.1 }],
};
const originalFile = {
  path: 'task.sql',
  sha: 'blob-v1',
  commit_sha: 'head-v1',
  content: 'SELECT 1;',
};

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('prodguild.admin.session.v1', 'organizer-conflict-test');
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'organizer-test', role: 'user', capabilities: ['organizer'] } }),
  );
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/competencies')
      return route.fulfill({ json: [{ id: 'sql', name: 'SQL', description: 'Запросы' }] });
    if (path === '/api/v1/admin/cases/conflict-case') return route.fulfill({ json: initial });
    if (path.endsWith('/reference-solution')) return route.fulfill({ status: 404, json: {} });
    if (path.endsWith('/files')) return route.fulfill({ json: [originalFile] });
    if (path.endsWith('/files/content')) return route.fulfill({ json: originalFile });
    return route.fulfill({ status: 404, json: {} });
  });
});

test('file conflict refreshes case version without replacing the draft or original blob SHA', async ({
  page,
}) => {
  let serverCase = initial;
  let serverFile = originalFile;
  const writes: { version: string | null; sha: string; content: string }[] = [];
  await page.route('**/api/v1/admin/cases/conflict-case', route =>
    route.fulfill({ json: serverCase }),
  );
  await page.route('**/api/v1/admin/cases/conflict-case/files', route =>
    route.fulfill({ json: [serverFile] }),
  );
  await page.route('**/api/v1/admin/cases/conflict-case/files/content?*', route => {
    if (route.request().method() !== 'PUT') return route.fulfill({ json: serverFile });
    const body = route.request().postDataJSON();
    writes.push({
      version: new URL(route.request().url()).searchParams.get('expected_case_version'),
      sha: body.sha,
      content: body.content,
    });
    if (writes.length === 1) {
      serverCase = { ...initial, version: 2 };
      return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
    }
    serverFile = { ...originalFile, sha: 'blob-v2', content: body.content };
    return route.fulfill({ json: serverFile });
  });
  await page.goto('/admin/conflict-case/edit?section=materials');
  const files = page.getByRole('region', { name: 'Материалы кейса', exact: true });
  const editor = files.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42;');
  await editor.press('ControlOrMeta+S');
  await expect(files.getByRole('alert')).toBeVisible();
  await expect(editor).toHaveText('SELECT 42;');
  await expect(editor).toBeEditable();
  await files.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(files.locator('.save-status')).toHaveText('Сохранено');
  expect(writes).toEqual([
    { version: '1', sha: 'blob-v1', content: 'SELECT 42;' },
    { version: '2', sha: 'blob-v1', content: 'SELECT 42;' },
  ]);
});

test('externally frozen case retains unsaved metadata for copying and download', async ({
  page,
}) => {
  let serverCase = initial;
  await page.route('**/api/v1/admin/cases/conflict-case', route => {
    if (route.request().method() === 'PATCH') {
      serverCase = { ...initial, frozen: true, version: 2 };
      return route.fulfill({ status: 409, json: { code: 'CASE_FROZEN' } });
    }
    return route.fulfill({ json: serverCase });
  });
  await page.goto('/admin/conflict-case/edit');
  const name = page.getByLabel('Название кейса', { exact: true });
  await name.fill('Мои несохранённые изменения');
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(name).toHaveValue('Мои несохранённые изменения');
  await expect(name).not.toBeEditable();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать моё описание', exact: true }).click();
  const exported = await download;
  expect(exported.suggestedFilename()).toBe('case-description.txt');
  const exportPath = await exported.path();
  if (!exportPath) throw new Error('Download was not saved');
  const contents = await readFile(exportPath, 'utf8');
  expect(contents).toContain('Мои несохранённые изменения');
  expect(contents).toContain(initial.description);
  expect(contents).toContain(initial.expected_result);
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Показать сохранённое описание', exact: true }).click();
  await expect(name).toHaveValue('Мои несохранённые изменения');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Показать сохранённое описание', exact: true }).click();
  await expect(name).toHaveCount(0);
  await expect(page.getByText(initial.description, { exact: true })).toBeVisible();
});

test('conflicting metadata requires comparison before overwriting the refreshed version', async ({
  page,
}) => {
  let serverCase = initial;
  const writes: { expected_version: number; title: string }[] = [];
  await page.route('**/api/v1/admin/cases/conflict-case', route => {
    if (route.request().method() !== 'PATCH') return route.fulfill({ json: serverCase });
    const body = route.request().postDataJSON();
    writes.push({ expected_version: body.expected_version, title: body.title });
    if (writes.length === 1) {
      serverCase = { ...initial, title: 'Изменения другого организатора', version: 2 };
      return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
    }
    serverCase = { ...serverCase, title: body.title, version: 3 };
    return route.fulfill({ json: serverCase });
  });
  await page.goto('/admin/conflict-case/edit');
  const name = page.getByLabel('Название кейса', { exact: true });
  await name.fill('Моя версия описания');
  await expect(
    page.getByRole('heading', { name: 'Описание изменено в другой вкладке' }),
  ).toBeVisible();
  await expect(name).toHaveValue('Моя версия описания');
  expect(writes).toHaveLength(1);
  await page.getByText('Описание на сервере', { exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Изменения другого организатора', level: 3 }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Оставить мои изменения', exact: true }).click();
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено');
  expect(writes).toEqual([
    { expected_version: 1, title: 'Моя версия описания' },
    { expected_version: 2, title: 'Моя версия описания' },
  ]);
});

for (const orphaned of [false, true]) {
  test(`locked file drafts can be exported and explicitly discarded (${orphaned ? 'deleted server file' : 'existing server file'})`, async ({
    page,
  }) => {
    let serverCase = initial;
    let locked = false;
    await page.route('**/api/v1/admin/cases/conflict-case', route =>
      route.fulfill({ json: serverCase }),
    );
    await page.route('**/api/v1/admin/cases/conflict-case/files', route =>
      route.fulfill({ json: orphaned && locked ? [] : [originalFile] }),
    );
    await page.route('**/api/v1/admin/cases/conflict-case/files/content?*', route => {
      if (route.request().method() !== 'PUT') return route.fulfill({ json: originalFile });
      locked = true;
      serverCase = { ...initial, version: 2, frozen: true };
      return route.fulfill({ status: 409, json: { code: 'CASE_FROZEN' } });
    });
    await page.goto('/admin/conflict-case/edit?section=materials');
    const files = page.getByRole('region', { name: 'Материалы кейса', exact: true });
    const editor = files.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
    await expect(editor).toHaveText('SELECT 1;');
    await editor.click();
    await editor.press('ControlOrMeta+A');
    await editor.pressSequentially('SELECT 42;');
    await editor.press('ControlOrMeta+S');
    await expect(files.getByRole('alert')).toBeVisible();
    const exportButton = files.getByRole('button', { name: 'Скачать все черновики', exact: true });
    await expect(exportButton).toBeEnabled();
    if (!orphaned) {
      await expect(editor).toHaveText('SELECT 42;');
      await expect(editor).not.toBeEditable();
    }
    const download = page.waitForEvent('download');
    await exportButton.click();
    const exported = await download;
    expect(exported.suggestedFilename()).toBe('file-drafts.json');
    const exportPath = await exported.path();
    if (!exportPath) throw new Error('Download was not saved');
    expect(JSON.parse(await readFile(exportPath, 'utf8'))).toEqual([
      { path: 'task.sql', content: 'SELECT 42;' },
    ]);
    page.once('dialog', dialog => dialog.dismiss());
    await files.getByRole('button', { name: 'Отменить изменения файлов', exact: true }).click();
    await expect(exportButton).toBeEnabled();
    page.once('dialog', dialog => dialog.accept());
    await files.getByRole('button', { name: 'Отменить изменения файлов', exact: true }).click();
    await expect(exportButton).toHaveCount(0);
    if (orphaned) await expect(editor).toHaveCount(0);
    else await expect(editor).toHaveText('SELECT 1;');
    await expect(page.getByRole('button', { name: 'Архивировать', exact: true })).toBeEnabled();
  });
}
