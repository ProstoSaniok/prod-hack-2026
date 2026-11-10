import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { expect, Request, test } from '@playwright/test';

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} for the coordinator-approved editor project.`);
  return value;
}

test('B1: real keyboard save and reload use only text-file APIs', async ({ page }, info) => {
  test.setTimeout(90000);
  expect(info.config.workers, 'The shared editor project must be tested serially.').toBe(1);
  const email = required('PRODGUILD_EDITOR_EMAIL');
  const password = required('PRODGUILD_EDITOR_PASSWORD');
  const project = required('PRODGUILD_EDITOR_PROJECT');
  const root = `/api/v1/projects/${encodeURIComponent(project)}`;
  const fileUrl = root + '/files/content?path=solution.sql';
  const attachmentRequests: string[] = [];
  const pageErrors: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (/attachments(?:\/|$)/.test(path)) attachmentRequests.push(path);
  });
  page.on('pageerror', error => pageErrors.push(error.message));

  await page.goto('/login?returnUrl=' + encodeURIComponent(`/projects/${project}`));
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project}$`));
  await page
    .locator('app-file-tree')
    .getByRole('button', { name: 'solution.sql', exact: true })
    .click();
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
  await expect(editor).toBeEditable();
  const token: string = await page.evaluate(() => {
    const session = JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}');
    if (typeof session.token !== 'string') throw new Error('Authenticated session required.');
    return session.token;
  });
  const headers = { Authorization: `Bearer ${token}` };
  const before = await page.request.get(fileUrl, { headers });
  expect(before.status()).toBe(200);
  const original: { content: string; sha: string } = await before.json();
  expect(typeof original.content).toBe('string');
  const text = `SELECT 20261018 AS editor_b1; -- ${info.project.name}`;
  let changed = false;
  try {
    await expect(page.getByRole('button', { name: 'Приложения', exact: true })).toHaveCount(0);
    await expect(page.locator('app-attachment-workspace')).toHaveCount(0);
    const saved = page.waitForResponse(
      response =>
        new URL(response.url()).pathname === root + '/files/content' &&
        response.request().method() === 'PUT',
    );
    await editor.click();
    changed = true;
    await editor.press('ControlOrMeta+A');
    await editor.pressSequentially(text, { delay: 12 });
    const response = await saved;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toMatchObject({ sha: original.sha, content: text });
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    await expect(editor).toHaveText(text);
    const read = await page.request.get(fileUrl, { headers });
    expect(read.status()).toBe(200);
    const persisted: { content: string; sha: string; commit_sha: string } = await read.json();
    expect(persisted.content).toBe(text);
    expect(persisted.sha).not.toBe(original.sha);
    await page.reload();
    await page
      .locator('app-file-tree')
      .getByRole('button', { name: 'solution.sql', exact: true })
      .click();
    await expect(editor).toHaveText(text);
    await expect(editor).toBeEditable();
    await expect(page.locator('app-file-tree')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Добавить файл', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Развернуть код', exact: true })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    expect(attachmentRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
    await page.screenshot({
      path: info.outputPath(`editor-b1-${info.project.name}.png`),
      fullPage: true,
    });
    const receipt = info.outputPath('text-api-receipt.json');
    await writeFile(
      receipt,
      JSON.stringify(
        {
          project,
          path: 'solution.sql',
          viewport: page.viewportSize(),
          putStatus: response.status(),
          getStatus: read.status(),
          content: persisted.content,
          sha: persisted.sha,
          commit: persisted.commit_sha,
          reloadMatches: true,
          attachmentRequests,
          overflow,
        },
        null,
        2,
      ),
    );
    await info.attach('text-api-receipt', {
      contentType: 'application/json',
      path: receipt,
    });
  } finally {
    if (changed) {
      // Cleanup uses the latest SHA and its own idempotency key; only this agreed file is restored.
      await page.route('**/api/v1/projects/*/files/content**', route =>
        route.request().method() === 'PUT' ? route.abort() : route.continue(),
      );
      const current = await page.request.get(fileUrl, { headers });
      expect(current.status()).toBe(200);
      const file: { sha: string; content: string } = await current.json();
      if (file.content !== original.content) {
        const restored = await page.request.put(fileUrl, {
          headers: { ...headers, 'Idempotency-Key': randomUUID() },
          data: { sha: file.sha, content: original.content },
        });
        expect(restored.status()).toBe(200);
      }
      const restoredRead = await page.request.get(fileUrl, { headers });
      expect(restoredRead.status()).toBe(200);
      expect((await restoredRead.json()).content).toBe(original.content);
      await writeFile(
        info.outputPath('restore-receipt.json'),
        JSON.stringify({ project, path: 'solution.sql', originalContentRestored: true }),
      );
    }
  }
});

test('B2: real file CRUD and a stale revision conflict preserve keyboard text', async ({
  page,
}, info) => {
  test.setTimeout(180000);
  expect(info.config.workers, 'The shared editor project must be tested serially.').toBe(1);
  const project = required('PRODGUILD_EDITOR_PROJECT');
  const root = `/api/v1/projects/${encodeURIComponent(project)}`;
  const path = `recovery-b2-${info.project.name}-${randomUUID().slice(0, 8)}.sql`;
  const fileUrl = root + '/files/content?path=' + encodeURIComponent(path);
  const writes = new Map<Request, { finished: Promise<void>; complete: () => void }>();
  page.on('request', request => {
    const url = new URL(request.url());
    const ownsPath =
      request.method() === 'POST'
        ? url.pathname === root + '/files' && request.postDataJSON()?.path === path
        : url.pathname === root + '/files/content' && url.searchParams.get('path') === path;
    if (!ownsPath || request.method() === 'GET') return;
    let complete: () => void = () => {};
    const finished = new Promise<void>(resolve => {
      complete = resolve;
    });
    writes.set(request, { finished, complete });
  });
  page.on('requestfinished', request => writes.get(request)?.complete());
  page.on('requestfailed', request => writes.get(request)?.complete());
  const attachmentRequests: string[] = [];
  const pageErrors: string[] = [];
  page.on('request', request => {
    if (/attachments(?:\/|$)/.test(new URL(request.url()).pathname))
      attachmentRequests.push(request.url());
  });
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.goto('/login?returnUrl=' + encodeURIComponent(`/projects/${project}`));
  await page.getByLabel('Email', { exact: true }).fill(required('PRODGUILD_EDITOR_EMAIL'));
  await page.getByLabel('Пароль', { exact: true }).fill(required('PRODGUILD_EDITOR_PASSWORD'));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${project}$`));
  const token: string = await page.evaluate(() => {
    const session = JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}');
    if (typeof session.token !== 'string') throw new Error('Authenticated session required.');
    return session.token;
  });
  const headers = { Authorization: `Bearer ${token}` };
  const solutionUrl = root + '/files/content?path=solution.sql';
  const originalRead = await page.request.get(solutionUrl, { headers });
  expect(originalRead.status()).toBe(200);
  const original: { content: string } = await originalRead.json();
  expect((await page.request.get(fileUrl, { headers })).status()).toBe(404);
  const editor = page.getByRole('textbox', { name: 'Редактор: ' + path, exact: true });
  const savedText = `SELECT 20261018 AS b2_saved; -- ${info.project.name}`;
  const localText = `SELECT 20261019 AS b2_local; -- ${info.project.name}`;
  const externalText = `SELECT 20261020 AS b2_external; -- ${info.project.name}`;
  const receipts: Record<string, unknown> = { project, path, viewport: page.viewportSize() };
  const responseFor = (method: string) =>
    page.waitForResponse(response => {
      const url = new URL(response.url());
      return (
        response.request().method() === method &&
        url.pathname === root + (method === 'POST' ? '/files' : '/files/content') &&
        (method === 'POST'
          ? response.request().postDataJSON()?.path === path
          : url.searchParams.get('path') === path)
      );
    });
  try {
    const created = responseFor('POST');
    await page.getByRole('button', { name: 'Добавить файл', exact: true }).click();
    const filename = page.getByRole('textbox', { name: 'Путь нового файла', exact: true });
    await filename.pressSequentially(path);
    await filename.press('Enter');
    const createResponse = await created;
    expect(createResponse.status()).toBe(201);
    receipts['createStatus'] = createResponse.status();
    await expect(editor).toBeEditable();
    const saved = responseFor('PUT');
    await editor.click();
    await editor.pressSequentially(savedText, { delay: 8 });
    expect((await saved).status()).toBe(200);
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    const savedRead = await page.request.get(fileUrl, { headers });
    expect(savedRead.status()).toBe(200);
    const savedFile: { sha: string; content: string } = await savedRead.json();
    expect(savedFile.content).toBe(savedText);
    await page.reload();
    await page.locator('app-file-tree').getByRole('button', { name: path, exact: true }).click();
    await expect(editor).toHaveText(savedText);
    receipts['saveAndReloadMatch'] = true;
    const external = await page.request.put(fileUrl, {
      headers: { ...headers, 'Idempotency-Key': randomUUID() },
      data: { sha: savedFile.sha, content: externalText },
    });
    expect(external.status()).toBe(200);
    const conflicted = responseFor('PUT');
    await editor.click();
    await editor.press('ControlOrMeta+A');
    await editor.pressSequentially(localText, { delay: 8 });
    const conflict = await conflicted;
    expect(conflict.status()).toBe(409);
    receipts['conflictStatus'] = conflict.status();
    await expect(editor).toHaveText(localText);
    await expect(page.getByRole('alert').first()).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.reload();
    await page.locator('app-file-tree').getByRole('button', { name: path, exact: true }).click();
    await expect(editor).toHaveText(localText);
    const downloading = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать все черновики', exact: true }).click();
    const exported = await (await downloading).path();
    if (!exported) throw new Error('Expected recovery export.');
    expect(JSON.parse(await readFile(exported, 'utf8'))).toContainEqual({
      path,
      content: localText,
    });
    receipts['conflictDraftSurvivedReloadAndExport'] = true;
    await page.screenshot({
      path: info.outputPath(`editor-b2-conflict-${info.project.name}.png`),
      fullPage: true,
    });
    await page
      .getByRole('button', { name: 'Загрузить серверную версию файла', exact: true })
      .click();
    await page.getByRole('button', { name: 'Заменить серверной версией', exact: true }).click();
    await expect(editor).toHaveText(externalText);
    await expect(page.getByRole('alert')).toHaveCount(0);
    const resaved = responseFor('PUT');
    await editor.click();
    await editor.press('ControlOrMeta+A');
    await editor.pressSequentially(savedText, { delay: 8 });
    expect((await resaved).status()).toBe(200);
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
    expect((await (await page.request.get(fileUrl, { headers })).json()).content).toBe(savedText);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    receipts['overflow'] = overflow;
    await page.screenshot({
      path: info.outputPath(`editor-b2-saved-${info.project.name}.png`),
      fullPage: true,
    });
    const deleted = responseFor('DELETE');
    await page.getByRole('button', { name: 'Удалить', exact: true }).click();
    await page.getByRole('button', { name: 'Подтвердить удаление файла', exact: true }).click();
    const deletion = await deleted;
    expect(deletion.status()).toBe(204);
    receipts['deleteStatus'] = deletion.status();
    expect((await page.request.get(fileUrl, { headers })).status()).toBe(404);
    await page.reload();
    await expect(
      page.locator('app-file-tree').getByRole('button', { name: path, exact: true }),
    ).toHaveCount(0);
    expect(attachmentRequests).toEqual([]);
    expect(pageErrors).toEqual([]);
    receipts['attachmentRequests'] = attachmentRequests;
    receipts['deletedAfterReload'] = true;
  } finally {
    // Block page-origin writes before cleanup; APIRequestContext remains available for this exact file.
    await page.route('**/api/v1/projects/*/files**', route =>
      route.request().method() === 'GET' ? route.continue() : route.abort(),
    );
    // Let already dispatched writes finish before reading a cleanup SHA. A lost response
    // is reconciled with its exact original URL/body/key, never a replacement operation.
    for (const [request, write] of [...writes]) {
      await write.finished;
      const response = await request.response();
      const status = response?.status() ?? 0;
      const body = response ? await response.json().catch(() => null) : null;
      const unknown =
        status === 0 ||
        status >= 500 ||
        (status === 409 &&
          ['OPERATION_IN_PROGRESS', 'OPERATION_NEEDS_ATTENTION'].includes(body?.code)) ||
        (status >= 200 &&
          status < 300 &&
          request.method() !== 'DELETE' &&
          (body?.path !== path || !body?.sha || !body?.commit_sha));
      if (!unknown) continue;
      const key = request.headers()['idempotency-key'];
      expect(key).toBeTruthy();
      const replay = await page.request.fetch(request.url(), {
        method: request.method(),
        headers: { ...headers, 'Idempotency-Key': key ?? '', 'Content-Type': 'application/json' },
        data: request.postData() ?? undefined,
        timeout: 100000,
      });
      expect(replay.ok(), 'Reconcile the exact browser write before cleanup.').toBe(true);
    }
    const remaining = await page.request.get(fileUrl, { headers });
    if (remaining.status() === 200) {
      const file: { sha: string } = await remaining.json();
      const removed = await page.request.delete(fileUrl + '&sha=' + encodeURIComponent(file.sha), {
        headers: { ...headers, 'Idempotency-Key': randomUUID() },
      });
      expect(removed.status()).toBe(204);
    } else expect(remaining.status()).toBe(404);
    expect((await page.request.get(fileUrl, { headers })).status()).toBe(404);
    const solution = await page.request.get(solutionUrl, { headers });
    expect(solution.status()).toBe(200);
    expect((await solution.json()).content).toBe(original.content);
    receipts['testFileRemoved'] = true;
    receipts['solutionUnchanged'] = true;
    await writeFile(info.outputPath('crud-api-receipt.json'), JSON.stringify(receipts, null, 2));
  }
});
