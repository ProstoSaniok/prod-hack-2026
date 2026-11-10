import { access, mkdir, writeFile } from 'node:fs/promises';
import { isAbsolute, join, resolve, sep } from 'node:path';
import { expect, Page, Request, test, TestInfo } from '@playwright/test';

// Run the write scenario once, on desktop, in the coordinator's C2 window.
// The second test only reads that existing submission at the other viewports.
// Never rerun the write scenario after an unknown submit result. Reconcile with
// GET project/submission first; any necessary replay must use the saved body/key.
// Keep the submission and receipts: this test intentionally has no cleanup.
const caseId = '052b016b-b966-42bf-b12b-41c8e7c4a0cd';
const excludedProject = 'd1cdf1ef-6d37-4c99-8586-633da45f2c9d';
const solution = 'SELECT 20261018 AS recovery_c2;';
const comment = 'C2: решение сохранено и проверено после перезагрузки.';
const filePath = 'solution.sql';
const editor = (page: Page) =>
  page.getByRole('textbox', { name: `Редактор: ${filePath}`, exact: true });

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} for the coordinator-approved C2 window.`);
  return value;
}

async function login(page: Page, target: string) {
  await page.goto('/login?returnUrl=' + encodeURIComponent(target));
  await page.getByLabel('Email', { exact: true }).fill('performer.local@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill(required('PRODGUILD_E2E_PASSWORD'));
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`${target}$`), { timeout: 30000 });
}

async function openFile(page: Page) {
  await page.locator('app-file-tree').getByRole('button', { name: filePath, exact: true }).click();
}

async function snapshot(page: Page, id: string, info: TestInfo) {
  const root = `/api/v1/submissions/${id}`;
  const read = (side: 'base' | 'head') =>
    page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === root + '/files/content' && url.searchParams.get('side') === side;
    });
  const headRead = read('head');
  await openFile(page);
  const headResponse = await headRead;
  expect(headResponse.status()).toBe(200);
  const head: { content: string; commit_sha: string; sha: string } = await headResponse.json();
  expect(head.content).toBe(solution);
  await expect(editor(page)).toHaveText(solution);
  await expect(editor(page)).toHaveAttribute('contenteditable', 'false');
  await expect(page.locator('app-attachment-workspace')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Добавить файл|Удалить файл/ })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('submission-head.png'), fullPage: true });

  await page.getByRole('button', { name: 'Исходные файлы', exact: true }).click();
  const baseRead = read('base');
  await openFile(page);
  const baseResponse = await baseRead;
  expect(baseResponse.status()).toBe(200);
  const base: { content: string; commit_sha: string; sha: string } = await baseResponse.json();
  expect(base.content).not.toBe(solution);
  expect(base.commit_sha).not.toBe(head.commit_sha);
  await expect(editor(page)).toHaveText(base.content);
  await page.screenshot({ path: info.outputPath('submission-base.png'), fullPage: true });

  const reloaded = page.waitForResponse(response => new URL(response.url()).pathname === root);
  await page.reload();
  const result: { commit_sha: string; base_commit_sha: string; summary: string } = await (
    await reloaded
  ).json();
  expect(result.commit_sha).toBe(head.commit_sha);
  expect(result.base_commit_sha).toBe(base.commit_sha);
  expect(result.summary).toBe(comment);
  const reloadHeadRead = read('head');
  await openFile(page);
  expect(await (await reloadHeadRead).json()).toMatchObject(head);
  await expect(editor(page)).toHaveText(solution);
  await expect(editor(page)).toHaveAttribute('contenteditable', 'false');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath('submission-reloaded.png'), fullPage: true });
  return { head, base, result, overflow };
}

async function guardWrites(
  page: Page,
  project?: string,
  beforeSubmit?: (request: Request) => Promise<void>,
) {
  const writes: { method: string; path: string; body: unknown }[] = [];
  const forbidden: string[] = [];
  const attachments: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (/\/attachments(?:\/|\?|$)/.test(request.url())) attachments.push(request.url());
  });
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'GET') return route.continue();
    const body: Record<string, unknown> = request.postDataJSON();
    const allowed =
      project &&
      ((request.method() === 'POST' &&
        url.pathname === '/api/v1/me/projects' &&
        body['case_id'] === caseId) ||
        (request.method() === 'PUT' &&
          url.pathname === `/api/v1/projects/${project}/files/content` &&
          url.searchParams.get('path') === filePath) ||
        (request.method() === 'POST' &&
          url.pathname === `/api/v1/projects/${project}/submissions`));
    if (!allowed) {
      forbidden.push(`${request.method()} ${url.pathname}`);
      return route.abort('blockedbyclient');
    }
    writes.push({ method: request.method(), path: url.pathname + url.search, body });
    if (url.pathname === `/api/v1/projects/${project}/submissions` && beforeSubmit) {
      try {
        // A failed journal write must prevent transmission to the real backend.
        await beforeSubmit(request);
      } catch (error) {
        await route.abort('blockedbyclient');
        throw error;
      }
    }
    return route.continue();
  });
  return { writes, forbidden, attachments, errors };
}

test('C2 live write: case to project, save, reload, cancel, confirm and immutable snapshot', async ({
  page,
}, info) => {
  test.setTimeout(150000);
  expect(info.project.name, 'Perform one submission, on desktop only.').toBe('desktop');
  expect(info.config.workers).toBe(1);
  expect(info.retry, 'Do not retry the entire write scenario. Reconcile the saved attempt.').toBe(
    0,
  );
  expect(info.project.retries).toBe(0);
  expect(required('PRODGUILD_C2_WRITE')).toBe('1');
  const project = required('PRODGUILD_C2_PROJECT');
  expect(project).toMatch(/^[0-9a-f-]{36}$/);
  expect(project).not.toBe(excludedProject);
  const root = `/api/v1/projects/${project}`;
  // Keep receipts outside Playwright output, which is cleaned on a subsequent run.
  const receiptDir = required('PRODGUILD_C2_RECEIPT_DIR');
  expect(isAbsolute(receiptDir)).toBe(true);
  const outputDir = resolve(info.project.outputDir);
  expect(resolve(receiptDir)).not.toBe(outputDir);
  expect(resolve(receiptDir).startsWith(outputDir + sep)).toBe(false);
  await mkdir(receiptDir, { recursive: true });
  const attemptPath = join(receiptDir, `${project}.request.json`);
  const receiptPath = join(receiptDir, `${project}.response.json`);
  const previousAttempt = await access(attemptPath).then(
    () => true,
    error => {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return false;
      throw error;
    },
  );
  expect(
    previousAttempt,
    'A submit attempt exists. Use GET reconciliation, not another write run.',
  ).toBe(false);
  const activity = await guardWrites(page, project, async request => {
    const idempotencyKey = request.headers()['idempotency-key'];
    if (!idempotencyKey) throw new Error('Submit must have an Idempotency-Key.');
    await writeFile(
      attemptPath,
      JSON.stringify(
        {
          project,
          caseId,
          method: request.method(),
          path: root + '/submissions',
          bodyText: request.postData(),
          body: request.postDataJSON(),
          idempotencyKey,
          recordedBeforeTransmission: true,
        },
        null,
        2,
      ),
      { flag: 'wx', mode: 0o600 },
    );
  });
  const caseResponse = page.waitForResponse(
    response => new URL(response.url()).pathname === `/api/v1/cases/${caseId}`,
  );
  await login(page, `/cases/${caseId}`);
  const study: { id: string; title: string } = await (await caseResponse).json();
  expect(study.id).toBe(caseId);
  await expect(page.getByRole('heading', { name: study.title, exact: true })).toBeVisible();
  const token: string = await page.evaluate(() => {
    const session = JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}');
    if (typeof session.token !== 'string') throw new Error('Authenticated participant required.');
    return session.token;
  });
  const headers = { Authorization: `Bearer ${token}` };
  const identityResponse = await page.request.get('/auth/me', { headers });
  expect(identityResponse.status()).toBe(200);
  const identity: { id: string; role: string } = await identityResponse.json();
  expect(identity.role).toBe('user');
  const preflightResponse = await page.request.get(root, { headers });
  expect(preflightResponse.status()).toBe(200);
  const preflight: { id: string; case_id: string; status: string; submission_id?: string } =
    await preflightResponse.json();
  expect(preflight).toMatchObject({ id: project, case_id: caseId, status: 'active' });
  expect(preflight.submission_id).toBeFalsy();
  await writeFile(
    info.outputPath('project-preflight.json'),
    JSON.stringify(
      {
        actor: identity.id,
        status: preflightResponse.status(),
        project: preflight,
      },
      null,
      2,
    ),
  );
  // The first business POST is allowed only after this owner's project passes GET preflight.
  const started = page.waitForResponse(
    response =>
      new URL(response.url()).pathname === '/api/v1/me/projects' &&
      response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Решать кейс', exact: true }).click();
  const startResponse = await started;
  expect([200, 201]).toContain(startResponse.status());
  expect(await startResponse.json()).toMatchObject({
    id: project,
    case_id: caseId,
    status: 'active',
  });
  await expect(page).toHaveURL(new RegExp(`/projects/${project}$`));
  await openFile(page);
  await expect(editor(page)).toBeEditable();
  const saved = page.waitForResponse(
    response =>
      new URL(response.url()).pathname === root + '/files/content' &&
      response.request().method() === 'PUT',
  );
  await editor(page).click();
  await editor(page).press('ControlOrMeta+A');
  await editor(page).pressSequentially(solution, { delay: 12 });
  const saveResponse = await saved;
  expect(saveResponse.status()).toBe(200);
  const savedFile: { commit_sha: string } = await saveResponse.json();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await page.reload();
  await openFile(page);
  await expect(editor(page)).toHaveText(solution);
  await page.screenshot({ path: info.outputPath('project-saved-reloaded.png'), fullPage: true });

  const prepare = page.getByRole('button', { name: 'Отправить решение', exact: true });
  const confirm = page.getByRole('button', { name: 'Подтвердить отправку', exact: true });
  await prepare.click();
  await expect(confirm).toBeEnabled();
  const summary = page.getByLabel('Комментарий к решению (необязательно)');
  await summary.fill(comment);
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await expect(editor(page)).toBeEditable();
  expect(activity.writes.filter(write => write.path.endsWith('/submissions'))).toEqual([]);
  await prepare.click();
  await expect(summary).toHaveValue(comment);
  await expect(confirm).toBeEnabled();
  await page.screenshot({ path: info.outputPath('submission-confirmation.png'), fullPage: true });
  const submitted = page
    .waitForResponse(
      response =>
        new URL(response.url()).pathname === root + '/submissions' &&
        response.request().method() === 'POST',
    )
    .then(async response => {
      // Save status and the raw receipt (including ID, when present) before any assertion.
      await writeFile(
        receiptPath,
        JSON.stringify(
          {
            project,
            status: response.status(),
            result: 'response-headers-received',
          },
          null,
          2,
        ),
        { flag: 'wx', mode: 0o600 },
      );
      let bodyText = '';
      let bodyReadError = '';
      try {
        bodyText = await response.text();
      } catch (error) {
        bodyReadError = error instanceof Error ? error.message : 'Could not read response body';
      }
      let result: unknown = null;
      try {
        result = JSON.parse(bodyText);
      } catch {
        // The raw response and original request remain available for reconciliation.
      }
      await writeFile(
        receiptPath,
        JSON.stringify(
          {
            project,
            status: response.status(),
            bodyText,
            bodyReadError,
            result,
          },
          null,
          2,
        ),
        { mode: 0o600 },
      );
      return response;
    });
  await confirm.click();
  const submitResponse = await submitted;
  expect(submitResponse.status()).toBe(201);
  const body = submitResponse.request().postDataJSON();
  expect(body).toEqual({ expected_commit_sha: savedFile.commit_sha, summary: comment });
  const result: { id: string; commit_sha: string; project_id: string } =
    await submitResponse.json();
  // Convenience copy; the durable request/receipt above survive failed assertions and reruns.
  await writeFile(
    info.outputPath('submission-created.json'),
    JSON.stringify({ project, result, body }, null, 2),
  );
  expect(result.project_id).toBe(project);
  expect(result.commit_sha).toBe(savedFile.commit_sha);
  await expect(page).toHaveURL(new RegExp(`/submissions/${result.id}$`));
  const evidence = await snapshot(page, result.id, info);
  const projectRead = page.waitForResponse(response => new URL(response.url()).pathname === root);
  await page.goto(`/projects/${project}`);
  expect(await (await projectRead).json()).toMatchObject({
    status: 'submitted',
    submission_id: result.id,
  });
  await openFile(page);
  await expect(editor(page)).toHaveAttribute('contenteditable', 'false');
  await expect(page.getByRole('button', { name: 'Отправить решение', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Открыть отправленное решение' }).click();
  await expect(page).toHaveURL(new RegExp(`/submissions/${result.id}$`));
  expect(activity.writes.filter(write => write.path.endsWith('/submissions'))).toHaveLength(1);
  expect(activity.forbidden).toEqual([]);
  expect(activity.attachments).toEqual([]);
  expect(activity.errors).toEqual([]);
  await writeFile(
    info.outputPath('submission-api-evidence.json'),
    JSON.stringify(
      {
        project,
        submission: result.id,
        saveStatus: saveResponse.status(),
        submitStatus: submitResponse.status(),
        ...evidence,
        ...activity,
      },
      null,
      2,
    ),
  );
});

test('C2 live read: existing submission remains immutable after reload', async ({ page }, info) => {
  test.setTimeout(90000);
  const id = required('PRODGUILD_C2_SUBMISSION');
  const activity = await guardWrites(page);
  await login(page, `/submissions/${id}`);
  const evidence = await snapshot(page, id, info);
  expect(activity.writes).toEqual([]);
  expect(activity.forbidden).toEqual([]);
  expect(activity.attachments).toEqual([]);
  expect(activity.errors).toEqual([]);
  await writeFile(
    info.outputPath('submission-api-evidence.json'),
    JSON.stringify(
      {
        submission: id,
        viewport: page.viewportSize(),
        ...evidence,
        ...activity,
      },
      null,
      2,
    ),
  );
});
