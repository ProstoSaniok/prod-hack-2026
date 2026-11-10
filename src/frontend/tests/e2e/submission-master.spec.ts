import { expect, Page, test } from '@playwright/test';

const projectId = 'c2-project';
const submissionId = 'c2-submission';
const baseSha = 'a'.repeat(40);
const headSha = 'b'.repeat(40);
const original = 'SELECT 1;';
const solution = 'SELECT 42 AS answer;';
const comment = 'Проверил результат запроса.';
const summary = (page: Page) => page.getByLabel('Комментарий к решению (необязательно)');
const editor = (page: Page) => page.getByRole('textbox', { name: 'Редактор: solution.sql' });
const prepare = (page: Page) =>
  page.getByRole('button', { name: 'Отправить решение', exact: true });
const confirm = (page: Page) =>
  page.getByRole('button', { name: 'Подтвердить отправку', exact: true });
const cancel = (page: Page) => page.getByRole('button', { name: 'Отмена', exact: true });

async function typeSolution(page: Page) {
  await editor(page).click();
  await editor(page).press('ControlOrMeta+A');
  await editor(page).pressSequentially(solution);
}

async function fixture(page: Page) {
  const state = {
    content: original,
    head: baseSha,
    submitted: false,
    snapshot: '',
    summary: '',
    failure: '' as
      | ''
      | '503'
      | '503-before-commit'
      | '401'
      | 'invalid-2xx'
      | 'disconnect'
      | 'NO_CHANGES'
      | 'COMMIT_CHANGED'
      | 'PROJECT_SUBMITTED',
    fileWrites: 0,
    submissions: [] as { body: Record<string, unknown>; key: string }[],
    attachments: [] as string[],
    errors: [] as string[],
  };
  const project = () => ({
    id: projectId,
    case_id: 'c2-case',
    case_version: 1,
    status: state.submitted ? 'submitted' : 'active',
    head_commit_sha: state.head,
    ...(state.submitted ? { submission_id: submissionId } : {}),
  });
  const submission = () => ({
    id: submissionId,
    project_id: projectId,
    case_id: 'c2-case',
    case_version: 1,
    status: 'submitted',
    submitted_at: '2026-10-18T12:00:00Z',
    commit_sha: headSha,
    base_commit_sha: baseSha,
    summary: state.summary,
  });
  const file = (content: string, sha: string) => ({
    path: 'solution.sql',
    sha,
    commit_sha: sha,
    content,
  });
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({
        token: 'c2-token',
        email: 'participant@example.com',
        name: 'Участник',
      }),
    ),
  );
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('request', request => {
    if (/\/attachments(?:\/|\?|$)/.test(request.url())) state.attachments.push(request.url());
  });
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: { id: 'c2-participant', role: 'user', capabilities: [] },
    }),
  );
  await page.route('**/api/v1/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const root = `/api/v1/projects/${projectId}`;
    const resultRoot = `/api/v1/submissions/${submissionId}`;
    if (/\/attachments(?:\/|$)/.test(url.pathname)) return route.fulfill({ status: 404, json: {} });
    if (url.pathname === root) return route.fulfill({ json: project() });
    if (url.pathname === '/api/v1/cases/c2-case')
      return route.fulfill({
        json: {
          id: 'c2-case',
          title: 'Отправка SQL',
          description: 'Исправьте запрос.',
          expected_result: 'Работающий запрос',
          difficulty: 'review_minus',
          content_version: 1,
          competencies: [],
        },
      });
    if (url.pathname === root + '/files' && request.method() === 'GET')
      return route.fulfill({ json: [file(state.content, state.head)] });
    if (url.pathname === root + '/files/content' && request.method() === 'GET')
      return route.fulfill({ json: file(state.content, state.head) });
    if (url.pathname === root + '/files/content' && request.method() === 'PUT') {
      expect(state.submitted).toBe(false);
      expect(request.postDataJSON().sha).toBe(state.head);
      state.fileWrites++;
      state.content = request.postDataJSON().content;
      state.head = headSha;
      return route.fulfill({ json: file(state.content, state.head) });
    }
    if (url.pathname === root + '/submissions' && request.method() === 'POST') {
      const body: Record<string, unknown> = request.postDataJSON();
      state.submissions.push({ body, key: request.headers()['idempotency-key'] ?? '' });
      const keys = Object.keys(body).sort();
      if (
        JSON.stringify(keys) !==
        JSON.stringify(
          body['summary'] === undefined
            ? ['expected_commit_sha']
            : ['expected_commit_sha', 'summary'],
        )
      )
        return route.fulfill({ status: 422, json: { detail: 'Extra inputs are not permitted' } });
      expect(body['expected_commit_sha']).toBe(state.head);
      if (state.failure === '503-before-commit' || state.failure === '401') {
        const status = state.failure === '401' ? 401 : 503;
        state.failure = '';
        return route.fulfill({ status, json: {} });
      }
      if (state.failure === 'NO_CHANGES' || state.failure === 'COMMIT_CHANGED') {
        const code = state.failure;
        state.failure = '';
        return route.fulfill({ status: 409, json: { code } });
      }
      if (state.failure === 'PROJECT_SUBMITTED') {
        state.submitted = true;
        state.snapshot = solution;
        state.summary = 'Отправлено в другой вкладке.';
        return route.fulfill({ status: 409, json: { code: 'PROJECT_SUBMITTED' } });
      }
      const replay = state.submitted;
      if (!replay) {
        state.submitted = true;
        state.snapshot = state.content;
        state.summary = String(body['summary'] ?? '');
      } else {
        expect(body).toEqual(state.submissions[0].body);
        expect(request.headers()['idempotency-key']).toBe(state.submissions[0].key);
      }
      const failure = state.failure;
      state.failure = '';
      if (failure === '503') return route.fulfill({ status: 503, json: {} });
      if (failure === 'invalid-2xx')
        return route.fulfill({ status: 201, json: { id: submissionId } });
      if (failure === 'disconnect') return route.abort('connectionreset');
      return route.fulfill({ status: replay ? 200 : 201, json: submission() });
    }
    if (url.pathname === resultRoot) return route.fulfill({ json: submission() });
    if (url.pathname === resultRoot + '/files')
      return route.fulfill({
        json: {
          entries: [
            {
              path: 'solution.sql',
              sha: url.searchParams.get('side') === 'base' ? baseSha : headSha,
            },
          ],
          next_offset: null,
        },
      });
    if (url.pathname === resultRoot + '/files/content')
      return route.fulfill({
        json:
          url.searchParams.get('side') === 'base'
            ? file(original, baseSha)
            : file(state.snapshot, headSha),
      });
    if (url.pathname === resultRoot + '/diff')
      return route.fulfill({
        json: {
          entries: [
            { comment_path: 'solution.sql', status: 'modified', additions: 1, deletions: 1 },
          ],
          next_offset: null,
        },
      });
    if (url.pathname === '/api/v1/me/review-mode')
      return route.fulfill({
        json: {
          enabled: false,
          can_review: false,
          status: 'disabled',
        },
      });
    if (url.pathname === '/api/v1/me/wallet')
      return route.fulfill({
        json: {
          settled: 10,
          reserved: 0,
          available: 10,
          unit: 'credit',
        },
      });
    if (url.pathname === '/api/v1/me/projects') return route.fulfill({ json: [project()] });
    expect(request.method()).toBe('GET');
    return route.fulfill({ json: [] });
  });
  await page.goto(`/projects/${projectId}`);
  await expect(editor(page)).toHaveText(original);
  return state;
}

test('cancel preserves saved file and summary without submitting; leaving warns about the comment', async ({
  page,
}) => {
  const state = await fixture(page);
  await typeSolution(page);
  await prepare(page).click();
  await expect(confirm(page)).toBeEnabled();
  expect(state.content).toBe(solution);
  expect(state.submissions).toEqual([]);
  await summary(page).fill(comment);
  await cancel(page).click();
  await expect(editor(page)).toHaveAttribute('contenteditable', 'true');
  await expect(editor(page)).toHaveText(solution);
  await prepare(page).click();
  await expect(summary(page)).toHaveValue(comment);
  await cancel(page).click();
  const dialog = page.waitForEvent('dialog').then(async dialog => {
    expect(dialog.message()).toContain('Комментарий к решению');
    await dialog.dismiss();
  });
  await page.locator('.project-heading').getByRole('link', { name: 'В работе' }).click();
  await dialog;
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await prepare(page).click();
  await expect(summary(page)).toHaveValue(comment);
  expect(state.submissions).toEqual([]);
  expect(state.attachments).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('strict master payload leads to an immutable snapshot after reload, without attachments', async ({
  page,
}, info) => {
  const state = await fixture(page);
  await typeSolution(page);
  await expect.poll(() => state.fileWrites).toBe(1);
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
  await page.reload();
  await expect(editor(page)).toHaveText(solution);
  await prepare(page).click();
  await expect(confirm(page)).toBeEnabled();
  // An empty optional summary must be omitted, not sent as null or an empty string.
  await confirm(page).click();
  await expect(page).toHaveURL(new RegExp(`/submissions/${submissionId}$`));
  expect(state.submissions).toHaveLength(1);
  expect(state.submissions[0].body).toEqual({ expected_commit_sha: headSha });
  expect(state.submissions[0].key).toBeTruthy();
  state.content = 'A later working-tree value must not replace the snapshot';
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  await expect(editor(page)).toHaveText(solution);
  await expect(editor(page)).toHaveAttribute('contenteditable', 'false');
  await page.getByRole('button', { name: 'Исходные файлы', exact: true }).click();
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  await expect(editor(page)).toHaveText(original);
  await page.reload();
  await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
  await expect(editor(page)).toHaveText(solution);
  await expect(page.locator('app-attachment-workspace')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Добавить файл|Удалить файл/ })).toHaveCount(0);
  expect(state.attachments).toEqual([]);
  expect(state.errors).toEqual([]);
  await page.screenshot({ path: info.outputPath('submission-snapshot.png'), fullPage: true });
});

for (const failure of ['503', 'invalid-2xx', 'disconnect'] as const) {
  test(`${failure}: retry retains the original body and key after a committed submission`, async ({
    page,
  }, info) => {
    const state = await fixture(page);
    await typeSolution(page);
    await prepare(page).click();
    await expect(confirm(page)).toBeEnabled();
    await summary(page).fill(comment);
    state.failure = failure;
    await confirm(page).click();
    const retry = page.getByRole('button', { name: 'Повторить отправку', exact: true });
    await expect(retry).toBeEnabled();
    await expect(summary(page)).toHaveValue(comment);
    await expect(summary(page)).toHaveAttribute('readonly', '');
    await expect(cancel(page)).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Повторить', exact: true })).toHaveCount(0);
    await page.locator('.project-heading').getByRole('link', { name: 'В работе' }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
    expect(state.submissions).toHaveLength(1);
    await page.screenshot({ path: info.outputPath('submission-retry.png'), fullPage: true });
    await retry.click();
    await expect(page).toHaveURL(new RegExp(`/submissions/${submissionId}$`));
    expect(state.submissions).toHaveLength(2);
    expect(state.submissions[1]).toEqual(state.submissions[0]);
    expect(state.submissions[0].body).toEqual({ expected_commit_sha: headSha, summary: comment });
    await expect(page.getByText(comment, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'solution.sql', exact: true }).click();
    await expect(editor(page)).toHaveText(solution);
    expect(state.attachments).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

for (const failure of ['NO_CHANGES', 'COMMIT_CHANGED'] as const) {
  test(`${failure}: a rejected submission keeps file and comment editable after cancel`, async ({
    page,
  }) => {
    const state = await fixture(page);
    await typeSolution(page);
    await prepare(page).click();
    await expect(confirm(page)).toBeEnabled();
    await summary(page).fill(comment);
    state.failure = failure;
    await confirm(page).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(cancel(page)).toBeEnabled();
    await expect(summary(page)).toHaveValue(comment);
    await expect(summary(page)).toBeEditable();
    await cancel(page).click();
    await expect(editor(page)).toHaveAttribute('contenteditable', 'true');
    await expect(editor(page)).toHaveText(solution);
    await prepare(page).click();
    await expect(summary(page)).toHaveValue(comment);
    await expect(confirm(page)).toBeEnabled();
    await confirm(page).click();
    await expect(page).toHaveURL(new RegExp(`/submissions/${submissionId}$`));
    expect(state.submissions).toHaveLength(2);
    expect(state.attachments).toEqual([]);
    expect(state.errors).toEqual([]);
  });
}

test('PROJECT_SUBMITTED resolves the server result when another tab already sent it', async ({
  page,
}) => {
  const state = await fixture(page);
  await typeSolution(page);
  await prepare(page).click();
  await expect(confirm(page)).toBeEnabled();
  await summary(page).fill(comment);
  state.failure = 'PROJECT_SUBMITTED';
  await confirm(page).click();
  await expect(page).toHaveURL(new RegExp(`/submissions/${submissionId}$`));
  await expect(page.getByText('Отправлено в другой вкладке.', { exact: true })).toBeVisible();
  expect(state.submissions).toHaveLength(1);
  expect(state.attachments).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('an uncertain attempt survives session expiry and SPA sign-in with the same body and key', async ({
  page,
}) => {
  const state = await fixture(page);
  await page.route('**/auth/sign_in/email', route =>
    route.fulfill({
      json: {
        access_token: 'c2-renewed-token',
        token_type: 'bearer',
      },
    }),
  );
  await typeSolution(page);
  await prepare(page).click();
  await expect(confirm(page)).toBeEnabled();
  await summary(page).fill(comment);
  state.failure = '503-before-commit';
  await confirm(page).click();
  const retry = page.getByRole('button', { name: 'Повторить отправку', exact: true });
  await expect(retry).toBeEnabled();
  state.failure = '401';
  await retry.click();
  const signIn = page.getByRole('link', { name: 'Войти и продолжить отправку' });
  await expect(signIn).toBeVisible();
  await expect(retry).toBeDisabled();
  page.once('dialog', dialog => dialog.accept());
  await signIn.click();
  await expect(page).toHaveURL(/\/login\?returnUrl=/);
  await page.getByLabel('Email', { exact: true }).fill('participant@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('Fixture-password!');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(summary(page)).toHaveValue(comment);
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(page).toHaveURL(new RegExp(`/submissions/${submissionId}$`));
  expect(state.submissions).toHaveLength(3);
  expect(state.submissions[1]).toEqual(state.submissions[0]);
  expect(state.submissions[2]).toEqual(state.submissions[0]);
  expect(state.attachments).toEqual([]);
  expect(state.errors).toEqual([]);
});

test('a definite precondition refusal after an unavailable response allows correction', async ({
  page,
}) => {
  const state = await fixture(page);
  await typeSolution(page);
  await prepare(page).click();
  await expect(confirm(page)).toBeEnabled();
  await summary(page).fill(comment);
  state.failure = '503-before-commit';
  await confirm(page).click();
  const retry = page.getByRole('button', { name: 'Повторить отправку', exact: true });
  await expect(retry).toBeEnabled();
  state.failure = 'COMMIT_CHANGED';
  await retry.click();
  await expect(page.getByRole('alert')).toContainText('Файлы изменились в другой вкладке');
  await expect(cancel(page)).toBeEnabled();
  await cancel(page).click();
  await expect(editor(page)).toHaveText(solution);
  await expect(editor(page)).toBeEditable();
  await prepare(page).click();
  await expect(summary(page)).toHaveValue(comment);
  await expect(confirm(page)).toBeEnabled();
  expect(state.submissions).toHaveLength(2);
  expect(state.attachments).toEqual([]);
  expect(state.errors).toEqual([]);
});
