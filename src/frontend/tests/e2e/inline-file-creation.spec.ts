import { expect, Page, test } from '@playwright/test';

interface FileRecord {
  path: string;
  sha: string;
  commit_sha: string;
  content: string;
}

async function fixture(page: Page, paths: readonly string[], uncertain = false) {
  const files: FileRecord[] = paths.map(path => ({
    path,
    sha: 'file-v1',
    commit_sha: 'head-v1',
    content: 'SELECT 1;',
  }));
  const creates: { body: { path: string; content: string }; key: string | undefined }[] = [];
  let version = 1;
  await page.addInitScript(() => {
    sessionStorage.setItem('prodguild.admin.session.v1', 'organizer-test');
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'organizer-test', role: 'user', capabilities: ['organizer'] } }),
  );
  await page.route('**/api/v1/**', route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/competencies') return route.fulfill({ json: [] });
    if (url.pathname === '/api/v1/admin/cases/inline-case')
      return route.fulfill({
        json: {
          id: 'inline-case',
          title: 'Файлы кейса',
          description: '',
          expected_result: '',
          difficulty: 'review_plus',
          created_at: '2026-10-17T10:00:00Z',
          version,
          frozen: false,
          status: 'draft',
          competencies: [],
        },
      });
    if (url.pathname.endsWith('/files/content'))
      return route.fulfill({
        json: files.find(file => file.path === url.searchParams.get('path')),
      });
    if (url.pathname.endsWith('/files')) {
      if (request.method() === 'POST') {
        const body = request.postDataJSON();
        creates.push({ body, key: request.headers()['idempotency-key'] });
        if (uncertain && creates.length === 1)
          return route.fulfill({ status: 503, json: { code: 'DEPENDENCY_UNAVAILABLE' } });
        const file = { ...body, sha: 'created-v1', commit_sha: 'head-v2' };
        files.push(file);
        version++;
        return route.fulfill({ status: 201, json: file });
      }
      return route.fulfill({ json: files });
    }
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
  await page.goto('/admin/inline-case/edit?section=materials');
  const workspace = page.locator('app-remote-files:visible');
  const add = workspace.getByRole('button', { name: 'Добавить файл', exact: true });
  await expect(add).toBeEnabled();
  return { workspace, add, creates };
}

test('empty tree creates inline with Enter and cancels with Escape without a request', async ({
  page,
}, info) => {
  const { workspace, add, creates } = await fixture(page, []);
  const input = workspace.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  await add.click();
  await expect(input).toBeFocused();
  await expect(workspace.locator('app-file-tree input')).toHaveCount(1);
  await input.pressSequentially('notes.md');
  await input.press('Escape');
  await expect(input).toHaveCount(0);
  await expect(add).toBeFocused();
  expect(creates).toHaveLength(0);

  await add.press('Enter');
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('');
  await input.click();
  await input.pressSequentially('src/solution.sql');
  await page.screenshot({ path: info.outputPath('inline-empty-tree.png'), fullPage: true });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
  await input.press('Enter');
  await expect(input).toHaveCount(0);
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: src/solution.sql', exact: true }),
  ).toBeVisible();
  expect(creates).toHaveLength(1);
  expect(creates[0].body).toEqual({ path: 'src/solution.sql', content: '' });
});

test('selected folder expands for creation and validates names before writing', async ({
  page,
}, info) => {
  const { workspace, add, creates } = await fixture(page, ['README.md', 'src/query.sql']);
  const folder = workspace.getByRole('button', { name: 'Папка src', exact: true });
  await folder.click();
  await expect(folder).toHaveAttribute('aria-expanded', 'false');
  await add.click();
  await expect(folder).toHaveAttribute('aria-expanded', 'true');
  const input = workspace.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  await expect(input).toBeFocused();
  await input.fill('query.sql');
  await input.press('Enter');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  await expect(workspace.getByRole('alert')).toHaveText('Файл с таким именем уже существует.');
  expect(creates).toHaveLength(0);
  await input.fill('../secret.sql');
  await input.press('Enter');
  await expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(creates).toHaveLength(0);
  await input.fill('answer.sql');
  await expect(input).not.toHaveAttribute('aria-invalid');
  await page.screenshot({ path: info.outputPath('inline-folder-tree.png'), fullPage: true });
  await input.press('Enter');
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: src/answer.sql', exact: true }),
  ).toBeVisible();
  expect(creates).toHaveLength(1);
  expect(creates[0].body.path).toBe('src/answer.sql');
});

test('uncertain creation keeps its filename and retries the exact request', async ({ page }) => {
  const { workspace, add, creates } = await fixture(page, ['src/query.sql'], true);
  await add.click();
  const input = workspace.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  await input.fill('notes.md');
  await input.press('Enter');
  await expect(workspace.getByRole('alert')).toBeVisible();
  await expect(input).toHaveValue('notes.md');
  await expect(input).not.toBeEditable();
  await expect(workspace.getByRole('button', { name: 'Создать файл', exact: true })).toBeDisabled();
  await expect(
    workspace.getByRole('button', { name: 'Выбрать корень файлов', exact: true }),
  ).toBeDisabled();
  await expect(
    workspace
      .locator('app-file-tree li')
      .nth(0)
      .getByRole('button', { name: 'Папка src', exact: true }),
  ).toBeVisible();
  await expect(
    workspace
      .locator('app-file-tree li')
      .nth(1)
      .getByRole('textbox', { name: 'Путь нового файла', exact: true }),
  ).toHaveValue('notes.md');
  await workspace.getByRole('button', { name: 'Повторить сохранение', exact: true }).click();
  await expect(input).toHaveCount(0);
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: src/notes.md', exact: true }),
  ).toBeVisible();
  expect(creates).toHaveLength(2);
  expect(creates[1]).toEqual(creates[0]);
  expect(creates[0].key).toBeTruthy();
  expect(creates[0].body.path).toBe('src/notes.md');
});

test('root selection and switching editor tabs choose the correct creation folder', async ({
  page,
}) => {
  const { workspace, add, creates } = await fixture(page, ['src/query.sql', 'docs/guide.md']);
  const input = workspace.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  const root = workspace.getByRole('button', { name: 'Выбрать корень файлов', exact: true });
  await root.focus();
  await root.press('Enter');
  await add.click();
  await input.fill('README.md');
  await input.press('Enter');
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: README.md', exact: true }),
  ).toBeVisible();
  expect(creates[0].body.path).toBe('README.md');

  await workspace.getByRole('button', { name: 'docs/guide.md', exact: true }).click();
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: docs/guide.md', exact: true }),
  ).toBeVisible();
  await workspace.getByRole('button', { name: 'Открытый файл src/query.sql', exact: true }).click();
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: src/query.sql', exact: true }),
  ).toBeVisible();
  await add.click();
  await input.fill('answer.sql');
  await input.press('Enter');
  await expect(
    workspace.getByRole('textbox', { name: 'Редактор: src/answer.sql', exact: true }),
  ).toBeVisible();
  expect(creates[1].body.path).toBe('src/answer.sql');
});
