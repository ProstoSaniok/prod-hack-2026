import { readFile } from 'node:fs/promises';
import { expect, Page, test } from '@playwright/test';

const attachmentRequestsByPage = new WeakMap<Page, string[]>();

test.afterEach(({ page }) => {
  expect(attachmentRequestsByPage.get(page) ?? []).toEqual([]);
});

interface FileRecord {
  path: string;
  sha: string;
  commit_sha: string;
  content: string;
}
interface Write {
  url: string;
  path: string;
  method: string;
  key: string;
  body: { sha?: string; content?: string; path?: string } | null;
}

async function fixture(
  page: Page,
  options: { failReads?: boolean; malformedReads?: boolean } = {},
) {
  const files = new Map<string, FileRecord>([
    [
      'first.sql',
      { path: 'first.sql', sha: 'first-v1', commit_sha: 'head-0', content: 'SELECT 1;' },
    ],
    [
      'second.sql',
      { path: 'second.sql', sha: 'second-v1', commit_sha: 'head-0', content: 'SELECT 2;' },
    ],
  ]);
  const writes: Write[] = [];
  const receipts = new Map<string, FileRecord | null>();
  const submissions: { expected_commit_sha: string }[] = [];
  const attachmentRequests: string[] = [];
  attachmentRequestsByPage.set(page, attachmentRequests);
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/\/attachments(?:\/|$)/.test(path)) attachmentRequests.push(`${request.method()} ${path}`);
  });
  const state = {
    files,
    writes,
    submissions,
    attachmentRequests,
    projectReads: [] as string[],
    head: 'head-0',
    conflict: false,
    failAfterCommit: false,
    failReads: options.failReads ?? false,
    malformedReads: options.malformedReads ?? false,
    failList: false,
    malformedSuccess: false,
    needsAttention: false,
    rejectCreate: 0,
    beforeWrite: async (_write: Write): Promise<void> => {},
    beforeRead: async (_path: string): Promise<void> => {},
  };
  const project = () => ({
    id: 'autosave-project',
    case_id: 'autosave-case',
    case_version: 1,
    status: 'active',
    head_commit_sha: state.head,
  });
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'autosave-token', email: 'autosave@example.com', name: 'Участник' }),
    );
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'autosave-user', role: 'user', capabilities: [] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const root = '/api/v1/projects/autosave-project';
    if (/\/attachments(?:\/|$)/.test(url.pathname))
      return route.fulfill({ status: 404, json: { detail: 'Not Found' } });
    if (url.pathname === root) {
      state.projectReads.push(state.head);
      return route.fulfill({ json: project() });
    }
    if (url.pathname === '/api/v1/cases/autosave-case')
      return route.fulfill({
        json: {
          id: 'autosave-case',
          title: 'Запросы SQL',
          description: 'Исправьте запросы.',
          expected_result: 'Работающие запросы',
          difficulty: 'performer',
          content_version: 1,
          competencies: [],
        },
      });
    if (url.pathname === root + '/files' && request.method() === 'GET')
      return route.fulfill({ status: state.failList ? 503 : 200, json: [...files.values()] });
    if (url.pathname === root + '/files/content' && request.method() === 'GET') {
      const file = files.get(url.searchParams.get('path') ?? '');
      await state.beforeRead(url.searchParams.get('path') ?? '');
      return route.fulfill({
        status: state.failReads ? 503 : file ? 200 : 404,
        json: state.malformedReads ? { ...file, content: null } : (file ?? {}),
      });
    }
    if (url.pathname.startsWith(root + '/files') && request.method() !== 'GET') {
      const write: Write = {
        url: url.pathname + url.search,
        path: url.searchParams.get('path') ?? '',
        method: request.method(),
        key: request.headers()['idempotency-key'] ?? '',
        body: request.postDataJSON(),
      };
      writes.push(write);
      await state.beforeWrite(write);
      if (state.needsAttention)
        return route.fulfill({ status: 409, json: { code: 'OPERATION_NEEDS_ATTENTION' } });
      if (write.method === 'POST' && state.rejectCreate)
        return route.fulfill({ status: state.rejectCreate, json: { code: 'INVALID_FILE' } });
      const cached = receipts.get(write.key);
      if (receipts.has(write.key))
        return route.fulfill(cached ? { json: cached } : { status: 204 });
      if (state.conflict) return route.fulfill({ status: 409, json: { code: 'FILE_CHANGED' } });
      const path = write.body?.path ?? write.path;
      const previous = files.get(path);
      if (write.method !== 'POST' && !previous)
        return route.fulfill({ status: 404, json: { code: 'FILE_NOT_FOUND' } });
      if (write.method === 'POST' && previous)
        return route.fulfill({ status: 409, json: { code: 'SOURCE_CONFLICT' } });
      const sha = write.body?.sha ?? url.searchParams.get('sha');
      if (write.method !== 'POST' && previous?.sha !== sha)
        return route.fulfill({ status: 409, json: { code: 'FILE_CHANGED' } });
      state.head = 'head-' + writes.length;
      if (write.method === 'DELETE') {
        files.delete(path);
        receipts.set(write.key, null);
        if (state.failAfterCommit) {
          state.failAfterCommit = false;
          return route.fulfill({ status: 503, json: {} });
        }
        return route.fulfill({ status: 204 });
      }
      const file: FileRecord = {
        path,
        sha: path + '-v' + (writes.length + 1),
        commit_sha: state.head,
        content: write.body?.content ?? '',
      };
      files.set(path, file);
      receipts.set(write.key, file);
      if (state.failAfterCommit) {
        state.failAfterCommit = false;
        return route.fulfill({ status: 503, json: {} });
      }
      if (state.malformedSuccess) {
        state.malformedSuccess = false;
        return route.fulfill({ json: { path, sha: '', commit_sha: '' } });
      }
      return route.fulfill({
        status: write.method === 'POST' ? 201 : 200,
        json: { ...file, content: null },
      });
    }
    if (url.pathname === root + '/submissions') {
      submissions.push(request.postDataJSON());
      return route.fulfill({
        json: {
          id: 'autosave-submission',
          project_id: 'autosave-project',
          case_id: 'autosave-case',
          case_version: 1,
          status: 'submitted',
          submitted_at: '2026-10-17T12:00:00Z',
          commit_sha: state.head,
          base_commit_sha: 'head-0',
        },
      });
    }
    if (url.pathname.endsWith('/me/review-mode'))
      return route.fulfill({ json: { enabled: false, can_review: false, status: 'disabled' } });
    if (url.pathname.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (url.pathname.endsWith('/me/projects')) return route.fulfill({ json: [project()] });
    if (url.pathname.endsWith('/files') || url.pathname.endsWith('/diff'))
      return route.fulfill({ json: { entries: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  await page.goto('/projects/autosave-project');
  if (!options.failReads && !options.malformedReads)
    await expect(editor(page, 'first.sql')).toHaveText('SELECT 1;');
  await expect(page.getByRole('heading', { name: 'Запросы SQL', exact: true })).toBeVisible();
  return state;
}

function editor(page: Page, path = 'first.sql') {
  return page.getByRole('textbox', { name: 'Редактор: ' + path, exact: true });
}

async function type(page: Page, content: string, path = 'first.sql'): Promise<void> {
  const field = editor(page, path);
  await field.click();
  await field.press('ControlOrMeta+A');
  await field.pressSequentially(content);
}

test('B1: text opens, autosaves and reloads without attachment requests', async ({ page }) => {
  const state = await fixture(page);
  const content = 'SELECT 42 AS answer; -- B1';
  await expect(page.locator('app-attachment-workspace')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Приложения', exact: true })).toHaveCount(0);
  const saved = page.waitForResponse(
    response =>
      new URL(response.url()).pathname === '/api/v1/projects/autosave-project/files/content' &&
      response.request().method() === 'PUT',
  );
  await type(page, content);
  expect((await saved).status()).toBe(200);
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  expect(state.files.get('first.sql')?.content).toBe(content);
  await page.reload();
  await expect(editor(page)).toHaveText(content);
  expect(state.attachmentRequests).toEqual([]);
});

test('B1: workspace flush opens confirmation and saves before navigation without attachments', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.clock.install({ time: new Date('2026-10-17T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-17T12:01:00Z'));
  await type(page, 'SELECT 43;');
  expect(state.writes).toHaveLength(0);
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку', exact: true }),
  ).toBeEnabled();
  expect(state.files.get('first.sql')?.content).toBe('SELECT 43;');
  expect(state.submissions).toHaveLength(0);
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await type(page, 'SELECT 44;');
  expect(state.writes).toHaveLength(1);
  await page.locator('.project-heading a').click();
  await expect(page).toHaveURL(/\/my-cases$/);
  expect(state.files.get('first.sql')?.content).toBe('SELECT 44;');
  expect(state.writes).toHaveLength(2);
  expect(state.submissions).toHaveLength(0);
  expect(state.attachmentRequests).toEqual([]);
  await page.clock.resume();
});

test('B1: UTF-8 upload uses text files and binary upload is rejected without attachment requests', async ({
  page,
}) => {
  const state = await fixture(page);
  const content = '# Проверка UTF-8\nТекст решения\n';
  const upload = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Загрузить файлы', exact: true }).click();
  await (
    await upload
  ).setFiles({
    name: 'notes.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(content, 'utf8'),
  });
  await expect(editor(page, 'notes.md')).toContainText('Текст решения');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]).toMatchObject({
    method: 'POST',
    body: { path: 'notes.md', content },
  });
  expect(state.files.get('notes.md')?.content).toBe(content);
  await page.reload();
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'notes.md', exact: true })
    .click();
  await expect(editor(page, 'notes.md')).toContainText('Текст решения');
  const rejected = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Загрузить файлы', exact: true }).click();
  await (
    await rejected
  ).setFiles({
    name: 'unsupported.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from([0, 1, 2, 3]),
  });
  await expect(page.getByRole('alert')).toContainText('UTF-8');
  await expect(editor(page, 'notes.md')).toContainText('Текст решения');
  expect(state.files.has('unsupported.bin')).toBe(false);
  expect(state.files.get('notes.md')?.content).toBe(content);
  expect(state.writes).toHaveLength(1);
  expect(state.attachmentRequests).toEqual([]);
});

test('debounces typing, keeps the editor alive and skips an unchanged buffer', async ({ page }) => {
  const state = await fixture(page);
  await page.clock.install({ time: new Date('2026-10-17T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-17T12:01:00Z'));
  await type(page, 'SELECT 42;');
  await page.clock.fastForward(799);
  expect(state.writes).toHaveLength(0);
  await page.clock.fastForward(1);
  await expect.poll(() => state.writes.length).toBe(1);
  await page.clock.resume();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await expect(editor(page)).toHaveText('SELECT 42;');
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  await type(page, 'SELECT 42;');
  await page.clock.fastForward(1000);
  expect(state.writes).toHaveLength(1);
});

test('serializes multiple files and retains typing while a save is in flight', async ({ page }) => {
  const state = await fixture(page);
  let release: () => void = () => {};
  const wait = new Promise<void>(resolve => {
    release = resolve;
  });
  state.beforeWrite = async () => {
    if (state.writes.length === 1) await wait;
  };
  await type(page, 'SELECT 10;');
  await editor(page).press('ControlOrMeta+s');
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'false');
  await type(page, 'SELECT 100;');
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'second.sql', exact: true })
    .click();
  await type(page, 'SELECT 200;', 'second.sql');
  expect(state.writes).toHaveLength(1);
  release();
  await expect.poll(() => state.files.get('first.sql')?.content).toBe('SELECT 100;');
  await expect.poll(() => state.files.get('second.sql')?.content).toBe('SELECT 200;');
  expect(state.writes[1]?.body?.sha).toBe('first.sql-v2');
  await expect(editor(page, 'second.sql')).toHaveText('SELECT 200;');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
});

test('replays the exact uncertain request after reload before newer edits', async ({ page }) => {
  const state = await fixture(page);
  state.failAfterCommit = true;
  await type(page, 'SELECT 10;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toContainText('Не удалось сохранить');
  await type(page, 'SELECT 100;');
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(editor(page)).toHaveText('SELECT 100;');
  await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect.poll(() => state.files.get('first.sql')?.content).toBe('SELECT 100;');
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.writes[2]?.key).not.toBe(state.writes[0]?.key);
  expect(state.writes[2]?.body?.sha).toBe('first.sql-v2');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
});

test('a read started before saving cannot replace the saved content with an older version', async ({
  page,
}) => {
  const state = await fixture(page);
  let releaseWrite: () => void = () => {};
  let releaseRead: () => void = () => {};
  const write = new Promise<void>(resolve => {
    releaseWrite = resolve;
  });
  const read = new Promise<void>(resolve => {
    releaseRead = resolve;
  });
  state.beforeWrite = () => write;
  await type(page, 'SELECT 10;');
  await editor(page).press('ControlOrMeta+s');
  await expect.poll(() => state.writes.length).toBe(1);
  let reading = false;
  state.beforeRead = async () => {
    reading = true;
    await read;
  };
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'first.sql', exact: true })
    .click();
  await expect.poll(() => reading).toBe(true);
  releaseWrite();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  releaseRead();
  await expect(editor(page)).toHaveText('SELECT 10;');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'false');
  await type(page, 'SELECT 100;');
  await editor(page).press('ControlOrMeta+s');
  await expect.poll(() => state.files.get('first.sql')?.content).toBe('SELECT 100;');
  expect(state.writes[1]?.body?.sha).toBe('first.sql-v2');
});

test('an uncertain deletion locks editing until its original request is resolved', async ({
  page,
}) => {
  const state = await fixture(page);
  state.failAfterCommit = true;
  await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить удаление файла', exact: true }).click();
  await expect(page.getByRole('alert').first()).toContainText('Не удалось сохранить');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(editor(page, 'second.sql')).toHaveText('SELECT 2;');
  expect(state.writes[1]).toEqual(state.writes[0]);
});

test('conflicts retain drafts and a failed replacement never discards them', async ({ page }) => {
  const state = await fixture(page);
  state.conflict = true;
  await type(page, 'SELECT 99;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toContainText('другой вкладке');
  state.failReads = true;
  await page.getByRole('button', { name: 'Загрузить серверную версию файла' }).click();
  await page.getByRole('button', { name: 'Заменить серверной версией' }).click();
  await expect(editor(page)).toHaveText('SELECT 99;');
  state.failReads = false;
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(editor(page)).toHaveText('SELECT 99;');
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Подтвердить отправку', exact: true })).toHaveCount(
    0,
  );
  expect(state.submissions).toHaveLength(0);
});

test('submission flushes all files and uses the refreshed project head', async ({ page }) => {
  const state = await fixture(page);
  await page.clock.install();
  await type(page, 'SELECT 10;');
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'second.sql', exact: true })
    .click();
  await type(page, 'SELECT 20;', 'second.sql');
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку', exact: true }),
  ).toBeEnabled();
  expect(state.files.get('first.sql')?.content).toBe('SELECT 10;');
  expect(state.files.get('second.sql')?.content).toBe('SELECT 20;');
  const expected = state.head;
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect.poll(() => state.submissions.length).toBe(1);
  expect(state.submissions[0]?.expected_commit_sha).toBe(expected);
});

test('immediate submission survives the project refresh from its pending file flush', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.clock.install({ time: new Date('2026-10-17T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-17T12:01:00Z'));
  let release: () => void = () => {};
  const pendingWrite = new Promise<void>(resolve => {
    release = resolve;
  });
  state.beforeWrite = () => pendingWrite;
  await type(page, 'SELECT 42;');
  expect(state.writes).toHaveLength(0);
  const confirmation = page.getByRole('button', {
    name: 'Подтвердить отправку',
    exact: true,
  });
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await expect.poll(() => state.writes.length).toBe(1);
  expect(state.writes[0]).toMatchObject({
    method: 'PUT',
    path: 'first.sql',
    body: { sha: 'first-v1', content: 'SELECT 42;' },
  });
  await expect(confirmation).toHaveCount(0);
  expect(state.submissions).toHaveLength(0);
  release();
  await expect.poll(() => state.projectReads).toContain('head-1');
  await expect(confirmation).toBeEnabled();
  await page.clock.fastForward(1000);
  expect(state.writes).toHaveLength(1);
  await page.clock.resume();
  await confirmation.click();
  await expect.poll(() => state.submissions.length).toBe(1);
  expect(state.submissions[0]?.expected_commit_sha).toBe('head-1');
  expect(state.writes).toHaveLength(1);
});

test('navigation flushes a pending draft and blocks leaving on a dismissed save failure', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.clock.install();
  await type(page, 'SELECT 10;');
  await page.locator('.project-heading a').click();
  await expect(page).toHaveURL(/\/my-cases$/);
  expect(state.files.get('first.sql')?.content).toBe('SELECT 10;');
  await page.goto('/projects/autosave-project');
  await expect(editor(page)).toHaveText('SELECT 10;');
  state.conflict = true;
  await type(page, 'SELECT 20;');
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('.project-heading a').click();
  await expect(page).toHaveURL(/\/projects\/autosave-project$/);
  await expect(editor(page)).toHaveText('SELECT 20;');
});

test('undo history survives autosaves and structural changes serialize after pending text', async ({
  page,
}) => {
  const state = await fixture(page);
  await type(page, 'SELECT 10;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await editor(page).press('ControlOrMeta+z');
  await expect(editor(page)).not.toHaveText('SELECT 10;');
  await type(page, 'SELECT 20;');
  await page.getByRole('button', { name: 'Добавить файл', exact: true }).click();
  const filename = page.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  await expect(filename).toBeFocused();
  await filename.pressSequentially('third.sql');
  await filename.press('Enter');
  await expect(editor(page, 'third.sql')).toBeVisible();
  expect(state.files.get('first.sql')?.content).toBe('SELECT 20;');
  expect(state.writes.at(-1)?.method).toBe('POST');
  await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить удаление файла', exact: true }).click();
  await expect.poll(() => state.files.has('third.sql')).toBe(false);
});

async function uploadText(page: Page, name: string, content: string): Promise<void> {
  const chosen = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Загрузить файлы', exact: true }).click();
  await (await chosen).setFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(content) });
}

async function exportedDrafts(page: Page): Promise<unknown> {
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать все черновики', exact: true }).click();
  const path = await (await downloading).path();
  if (!path) throw new Error('Draft export was not downloaded.');
  return JSON.parse(await readFile(path, 'utf8'));
}

for (const failure of ['unavailable', 'malformed'] as const) {
  test(`B2: ${failure} first read cannot accept untracked keyboard input`, async ({ page }) => {
    const state = await fixture(page, {
      failReads: failure === 'unavailable',
      malformedReads: failure === 'malformed',
    });
    await expect(page.getByRole('alert').first()).toBeVisible();
    await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
    await expect(page.getByText('Файл не загружен', { exact: true })).toBeVisible();
    await editor(page).click();
    await editor(page).pressSequentially('must not disappear');
    await expect(editor(page)).toHaveText('');
    expect(state.writes).toHaveLength(0);
    state.failReads = false;
    state.malformedReads = false;
    await page.getByRole('button', { name: 'Повторить загрузку', exact: true }).click();
    await expect(editor(page)).toHaveText('SELECT 1;');
    await expect(editor(page)).toHaveAttribute('aria-readonly', 'false');
    await type(page, 'SELECT 2002;');
    await editor(page).press('ControlOrMeta+s');
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    expect(state.files.get('first.sql')?.content).toBe('SELECT 2002;');
  });
}

test('B2: a rejected binary upload does not block a subsequent valid text upload', async ({
  page,
}) => {
  const state = await fixture(page);
  await uploadText(page, 'binary.bin', '\0invalid');
  await expect(page.getByRole('alert').first()).toContainText('UTF-8');
  await uploadText(page, 'valid.txt', 'Текст после отказа');
  await expect(editor(page, 'valid.txt')).toHaveText('Текст после отказа');
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(state.files.get('valid.txt')?.content).toBe('Текст после отказа');
  expect(state.writes).toHaveLength(1);
});

test('B2: a successful file read clears a stale error and allows navigation flush', async ({
  page,
}) => {
  const state = await fixture(page);
  state.failReads = true;
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'second.sql', exact: true })
    .click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  state.failReads = false;
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'first.sql', exact: true })
    .click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await type(page, 'SELECT 202;');
  await page.locator('.project-heading a').click();
  await expect(page).toHaveURL(/\/my-cases$/);
  expect(state.files.get('first.sql')?.content).toBe('SELECT 202;');
});

for (const status of [409, 422]) {
  test(`B2: rejected upload ${status} retains text through reload and explicit retry`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.rejectCreate = status;
    const content = 'Сохранить текст загруженного файла';
    await uploadText(page, 'recovered.txt', content);
    await expect(page.getByRole('alert').first()).toBeVisible();
    expect(await exportedDrafts(page)).toEqual([{ path: 'recovered.txt', content }]);
    page.once('dialog', dialog => dialog.accept());
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Повторить сохранение', exact: true }),
    ).toBeVisible();
    expect(await exportedDrafts(page)).toEqual([{ path: 'recovered.txt', content }]);
    expect(state.writes).toHaveLength(1);
    state.rejectCreate = 0;
    await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
    await expect(editor(page, 'recovered.txt')).toHaveText(content);
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    expect(state.writes[1]?.body).toEqual(state.writes[0]?.body);
    expect(state.writes[1]?.key).not.toBe(state.writes[0]?.key);
  });
}

test('B2: ambiguous upload remains exportable and replays the original POST after reload', async ({
  page,
}) => {
  const state = await fixture(page);
  state.failAfterCommit = true;
  const content = 'Текст неопределённого создания';
  await uploadText(page, 'uncertain.txt', content);
  await expect(page.getByRole('alert').first()).toBeVisible();
  expect(await exportedDrafts(page)).toEqual([{ path: 'uncertain.txt', content }]);
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  expect(await exportedDrafts(page)).toEqual([{ path: 'uncertain.txt', content }]);
  await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(editor(page, 'uncertain.txt')).toHaveText(content);
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toEqual(state.writes[0]);
});

test('B2: empty revision in a successful response preserves the exact request and newer text', async ({
  page,
}) => {
  const state = await fixture(page);
  state.malformedSuccess = true;
  await type(page, 'SELECT 2020;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toBeVisible();
  await type(page, 'SELECT 2021;');
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(editor(page)).toHaveText('SELECT 2021;');
  await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.files.get('first.sql')?.content).toBe('SELECT 2021;');
});

for (const operation of ['save', 'delete'] as const) {
  test(`B2: ${operation} needing reconciliation keeps its exact request through reload`, async ({
    page,
  }) => {
    const state = await fixture(page);
    state.failAfterCommit = true;
    if (operation === 'save') {
      await type(page, 'SELECT 2022;');
      await editor(page).press('ControlOrMeta+s');
    } else {
      await page.getByRole('button', { name: 'Удалить', exact: true }).click();
      await page.getByRole('button', { name: 'Подтвердить удаление файла', exact: true }).click();
    }
    await expect(page.getByRole('alert').first()).toBeVisible();
    state.needsAttention = true;
    await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
    await expect.poll(() => state.writes.length).toBe(2);
    await expect(
      page.getByRole('button', { name: 'Повторить сохранение', exact: true }),
    ).toBeEnabled();
    page.once('dialog', dialog => dialog.accept());
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Повторить сохранение', exact: true }),
    ).toBeVisible();
    state.needsAttention = false;
    await page.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(3);
    expect(state.writes[1]).toEqual(state.writes[0]);
    expect(state.writes[2]).toEqual(state.writes[0]);
  });
}

test('B2: an orphan draft stays exportable and explicit discard unblocks other files', async ({
  page,
}) => {
  const state = await fixture(page);
  state.conflict = true;
  await type(page, 'SELECT 2023;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toContainText('другой вкладке');
  state.files.delete('first.sql');
  state.conflict = false;
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(editor(page, 'second.sql')).toHaveText('SELECT 2;');
  await expect(page.getByRole('alert').first()).toBeVisible();
  expect(await exportedDrafts(page)).toEqual([{ path: 'first.sql', content: 'SELECT 2023;' }]);
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Отменить изменения файлов', exact: true }).click();
  expect(await exportedDrafts(page)).toEqual([{ path: 'first.sql', content: 'SELECT 2023;' }]);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Отменить изменения файлов', exact: true }).click();
  state.conflict = false;
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(editor(page, 'second.sql')).toBeEditable();
  await type(page, 'SELECT 2024;', 'second.sql');
  await editor(page, 'second.sql').press('ControlOrMeta+s');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  expect(state.files.get('second.sql')?.content).toBe('SELECT 2024;');
});

test('B2: failed file list after explicit discard cannot re-enable an untracked editor', async ({
  page,
}) => {
  const state = await fixture(page);
  state.conflict = true;
  await type(page, 'SELECT 2025;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toBeVisible();
  state.failList = true;
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Отменить изменения файлов', exact: true }).click();
  await expect(page.getByText('Файл не загружен', { exact: true })).toBeVisible();
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  await editor(page).click();
  await editor(page).pressSequentially('untracked');
  await expect(editor(page)).not.toContainText('untracked');
  state.failList = false;
  state.conflict = false;
  await page.getByRole('button', { name: 'Повторить загрузку', exact: true }).click();
  await expect(editor(page)).toHaveText('SELECT 1;');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'false');
  await type(page, 'SELECT 2026;');
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  expect(state.files.get('first.sql')?.content).toBe('SELECT 2026;');
});

test('B2: a delayed explicit replacement cannot roll back a newer successful save', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.clock.install({ time: new Date('2026-10-18T12:00:00Z') });
  await page.clock.pauseAt(new Date('2026-10-18T12:01:00Z'));
  await type(page, 'SELECT 2027;');
  state.failReads = true;
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'first.sql', exact: true })
    .click();
  await expect(page.getByRole('alert').first()).toBeVisible();
  state.failReads = false;
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => {
    release = resolve;
  });
  let reading = false;
  state.beforeRead = async () => {
    reading = true;
    await pending;
  };
  await page.getByRole('button', { name: 'Загрузить серверную версию файла', exact: true }).click();
  await page.getByRole('button', { name: 'Заменить серверной версией', exact: true }).click();
  await expect.poll(() => reading).toBe(true);
  await page.clock.fastForward(800);
  await expect.poll(() => state.files.get('first.sql')?.content).toBe('SELECT 2027;');
  release();
  await expect(editor(page)).toHaveText('SELECT 2027;');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'false');
  await page.clock.resume();
  await type(page, 'SELECT 2028;');
  await editor(page).press('ControlOrMeta+s');
  await expect.poll(() => state.files.get('first.sql')?.content).toBe('SELECT 2028;');
  expect(state.writes[1]?.body?.sha).toBe('first.sql-v2');
});
