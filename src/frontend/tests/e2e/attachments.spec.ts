import { expect, Page, test } from '@playwright/test';

type FileRecord = {
  id: string;
  path: string;
  size: number;
  media_type: string;
  text: boolean;
  sha256: string;
  download_url: string;
  bytes: Buffer;
};

async function fixture(page: Page, kind: 'review' | 'quality' | 'project') {
  let version = kind === 'project' ? 0 : 1;
  let sequence = 0;
  const state = {
    files: [] as FileRecord[],
    loseNext: false,
    rejectNext: false,
    keys: [] as string[],
    sentVersion: -1,
    source: 'SELECT 1;',
    beforeUpload: async (_path: string): Promise<void> => {},
    beforeSource: async (): Promise<void> => {},
    bumpVersion: () => {
      version++;
    },
  };
  const draft = {
    version: 1,
    score: 85,
    verdict: 'approved',
    feedback: {
      strengths: 'Верное решение',
      problems: 'Ошибок нет',
      next_steps: 'Продолжайте практику',
    },
    comments: [],
  };
  const task = {
    id: 'attachment-task',
    submission_id: 'submission-one',
    kind: kind === 'quality' ? 'quality_check' : 'review',
    review_id: kind === 'quality' ? 'review-one' : null,
    revision_id: kind === 'quality' ? 'revision-one' : null,
    status: 'assigned',
    can_continue: true,
    lease_expires_at: null,
  };
  const study = {
    id: 'case-one',
    title: 'Файлы решения',
    description: 'Проверьте решение.',
    expected_result: 'SQL и пояснения',
    difficulty: 'review_minus',
    content_version: 1,
    competencies: [],
  };
  const review = {
    id: 'review-one',
    assignment_id: task.id,
    reviewer_id: 'reviewer',
    submission_id: task.submission_id,
    status: 'published',
    current_revision: { ...draft, id: 'revision-one' },
  };
  const root =
    kind === 'project' ? '/api/v1/projects/project-one' : '/api/v1/review-assignments/' + task.id;
  const manifest = (empty = false) => ({
    version,
    items: empty ? [] : state.files.map(({ bytes: _bytes, ...file }) => file),
    total_bytes: empty ? 0 : state.files.reduce((sum, file) => sum + file.size, 0),
    max_file_bytes: 10485760,
    max_total_bytes: 52428800,
    max_files: 100,
  });
  const receipts = new Map<string, ReturnType<typeof manifest>>();
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'attachment-test', email: 'review@example.com', name: 'Участник' }),
    ),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'attachment-user', role: 'user', capabilities: ['participant'] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname,
      method = request.method();
    if (path.endsWith('/review-mode'))
      return route.fulfill({
        json: {
          enabled: true,
          can_review: kind !== 'project',
          can_quality_review: kind === 'quality',
          can_assess_readiness: kind === 'quality',
          status: 'assigned',
          tasks: kind === 'project' ? [] : [task],
        },
      });
    if (path.endsWith('/wallet'))
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (path === '/api/v1/projects/project-one')
      return route.fulfill({
        json: {
          id: 'project-one',
          case_id: 'case-one',
          case_version: 1,
          status: 'active',
          head_commit_sha: 'head-one',
          attachments_version: version,
        },
      });
    if (path === '/api/v1/cases/case-one') return route.fulfill({ json: study });
    if (path === '/api/v1/me/review-assignments') return route.fulfill({ json: [task] });
    if (path === '/api/v1/review-assignments/' + task.id) return route.fulfill({ json: task });
    if (path.endsWith('/workspace'))
      return route.fulfill({
        json: { assignment: task, case: study, draft: { ...draft, version } },
      });
    if (path.endsWith('/source-review'))
      return route.fulfill({
        json: {
          ...review,
          status: 'pending_quality_check',
          workspace: { assignment: task, case: study, draft: { ...draft, version } },
        },
      });
    if (path.endsWith('/source-attachments') || path.endsWith('/submission-attachments'))
      return route.fulfill({ json: manifest(true) });
    if (path.endsWith('/attachments') && method === 'GET')
      return route.fulfill({
        json: manifest(
          path !== root + '/attachments' &&
            !path.includes('review-revisions') &&
            !path.includes('quality-assessments'),
        ),
      });
    if (path.startsWith(root + '/attachments') && method !== 'GET') {
      const key = request.headers()['idempotency-key'];
      state.keys.push(key);
      await state.beforeUpload(url.searchParams.get('path') ?? '');
      const replay = receipts.get(key);
      if (replay) return route.fulfill({ json: replay });
      if (state.rejectNext) {
        state.rejectNext = false;
        return route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
      }
      if (Number(url.searchParams.get('expected_version')) !== version)
        return route.fulfill({
          status: 409,
          json: { code: 'STALE_VERSION', message: 'Версия изменена' },
        });
      const id = path.split('/').at(-1) ?? '';
      if (method === 'DELETE') state.files = state.files.filter(file => file.id !== id);
      else {
        const previous = state.files.find(file => file.id === id);
        const bytes = request.postDataBuffer() ?? Buffer.alloc(0);
        const fileId = previous?.id ?? `file-${++sequence}`;
        const filePath = previous?.path ?? url.searchParams.get('path') ?? '';
        const file: FileRecord = {
          id: fileId,
          path: filePath,
          size: bytes.length,
          media_type: url.searchParams.get('media_type') ?? 'text/plain',
          text: !bytes.includes(0) && !filePath.endsWith('.pdf') && bytes.length <= 1048576,
          sha256: `sha-${version + 1}`,
          download_url: root + '/attachments/' + fileId + '/content',
          bytes,
        };
        state.files = [...state.files.filter(item => item.id !== fileId), file];
      }
      version++;
      const result = manifest();
      receipts.set(key, result);
      if (state.loseNext) {
        state.loseNext = false;
        return route.abort('failed');
      }
      return route.fulfill({ json: result });
    }
    if (path.startsWith(root + '/attachments/') && path.endsWith('/content')) {
      const file = state.files.find(item => item.download_url === path);
      return file
        ? route.fulfill({ body: file.bytes, contentType: 'application/octet-stream' })
        : route.fulfill({ status: 404 });
    }
    if (path.endsWith('/draft')) {
      if (method === 'PUT') {
        const body = request.postDataJSON();
        expect(body.expected_version).toBe(version);
        Object.assign(draft, body);
        version++;
      }
      return route.fulfill({ json: { ...draft, version } });
    }
    if (path.endsWith('/quality-assessments') && method === 'POST') {
      const body = request.postDataJSON();
      state.sentVersion = body.expected_draft_version;
      return route.fulfill({
        json: {
          id: 'assessment-one',
          revision_id: 'revision-one',
          decision: body.decision,
          score: body.score,
          feedback: body.feedback,
        },
      });
    }
    if (path === root + '/reviews' && method === 'POST') {
      state.sentVersion = request.postDataJSON().expected_draft_version;
      return route.fulfill({
        json: { id: 'review-one', submission_id: task.submission_id, status: 'published' },
      });
    }
    if (path === root + '/submissions' && method === 'POST') {
      state.sentVersion = request.postDataJSON().expected_attachments_version;
      return route.fulfill({
        json: {
          id: 'submission-one',
          project_id: 'project-one',
          case_id: 'case-one',
          case_version: 1,
          status: 'submitted',
          submitted_at: '2026-10-18T12:00:00Z',
          commit_sha: 'head-one',
          base_commit_sha: 'base-one',
        },
      });
    }
    if (path === '/api/v1/reviews/review-one') return route.fulfill({ json: review });
    if (path.endsWith('/files')) {
      if (method === 'POST') {
        await state.beforeSource();
        const body = request.postDataJSON();
        state.source = body.content;
        return route.fulfill({
          json: {
            path: body.path,
            content: body.content,
            sha: 'source-new',
            commit_sha: 'head-one',
          },
        });
      }
      const files = [
        {
          path: 'solution.sql',
          sha: 'source-one',
          commit_sha: 'head-one',
          size: state.source.length,
        },
      ];
      return route.fulfill({
        json: kind === 'project' ? files : { entries: files, next_offset: null },
      });
    }
    if (path.endsWith('/files/content'))
      return route.fulfill({
        json: {
          path: url.searchParams.get('path') ?? 'solution.sql',
          content: state.source,
          sha: 'source-one',
          commit_sha: 'head-one',
        },
      });
    if (path.endsWith('/diff')) return route.fulfill({ json: { entries: [], next_offset: null } });
    if (path.endsWith('/me/reviews') || path.endsWith('/quality-assessments'))
      return route.fulfill({ json: { items: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  await page.goto(
    kind === 'project'
      ? '/projects/project-one'
      : (kind === 'quality' ? '/expert/' : '/reviews/') + task.id,
  );
  if (kind === 'review') {
    await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
    await page.getByRole('button', { name: 'Приложения к отзыву', exact: true }).click();
  }
  if (kind === 'quality')
    await page.getByRole('button', { name: 'Приложения к заключению', exact: true }).click();
  return state;
}

test('reviewer creates editable attachment, uploads binary and retains files after reload and maximize', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel.getByRole('button', { name: 'Создать файл', exact: true }).click();
  await panel.getByLabel('Путь нового приложения').fill('example.py');
  await panel.getByRole('button', { name: 'Создать приложение', exact: true }).click();
  const editor = panel.getByRole('textbox', { name: 'Редактор: example.py', exact: true });
  await editor.click();
  await editor.pressSequentially('print(42)', { delay: 30 });
  await expect.poll(() => state.files[0]?.bytes.toString()).toBe('print(42)');
  await panel.locator('input[type=file]').setInputFiles({
    name: 'notes.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.7\n\0binary'),
  });
  await expect.poll(() => state.files.length).toBe(2);
  await page.reload();
  await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
  await page.getByRole('button', { name: 'Приложения к отзыву', exact: true }).click();
  await panel.getByRole('button', { name: 'example.py', exact: true }).click();
  await expect(editor).toHaveText('print(42)');
  await page.getByRole('button', { name: 'Развернуть код', exact: true }).click();
  await expect(page.locator('app-sidebar')).toBeHidden();
  await expect(editor).toHaveText('print(42)');
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).click();
  expect(state.source).toBe('SELECT 1;');
});

test('uncertain binary upload survives reload and repeats the exact request once', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  state.loseNext = true;
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel.locator('input[type=file]').setInputFiles({
    name: 'image.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from([0, 1, 2, 255]),
  });
  await expect(
    panel.getByRole('button', { name: 'Повторить сохранение приложений' }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
  await page.getByRole('button', { name: 'Приложения к отзыву', exact: true }).click();
  await panel.getByRole('button', { name: 'Повторить сохранение приложений' }).click();
  await expect(panel.getByRole('button', { name: 'image.bin', exact: true })).toBeVisible();
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await expect.poll(() => state.keys.length).toBe(2);
  expect(state.keys[0]).toBe(state.keys[1]);
  expect(state.files).toHaveLength(1);
});

test('expert sends the attachment version with the private assessment', async ({ page }) => {
  const state = await fixture(page, 'quality');
  const panel = page.getByRole('region', { name: 'Приложения к заключению', exact: true });
  await panel.locator('input[type=file]').setInputFiles({
    name: 'example.sql',
    mimeType: 'text/plain',
    buffer: Buffer.from('SELECT 42;'),
  });
  await expect.poll(() => state.files.length).toBe(1);
  await page
    .getByLabel('Комментарий эксперта', { exact: true })
    .fill('Проверка выполнена корректно. Пример приложен.');
  await page.getByRole('button', { name: 'Сохранить экспертное решение', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить экспертное решение', exact: true }).click();
  await expect.poll(() => state.sentVersion).toBe(2);
  await expect(
    page.getByRole('status').filter({ hasText: 'Экспертная оценка сохранена' }),
  ).toBeVisible();
});

test('participant uploads source text and binary into the corresponding groups', async ({
  page,
}) => {
  const state = await fixture(page, 'project');
  await expect(
    page.locator('app-remote-files').getByRole('button', { name: 'Загрузить файлы', exact: true }),
  ).toBeEnabled();
  await page
    .locator('app-remote-files input[type=file]')
    .setInputFiles({ name: 'answer.py', mimeType: 'text/plain', buffer: Buffer.from('print(42)') });
  await expect.poll(() => state.source).toBe('print(42)');
  await page.locator('app-remote-files input[type=file]').setInputFiles({
    name: 'diagram.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF\0bytes'),
  });
  await expect.poll(() => state.files.length).toBe(1);
  await expect(
    page
      .getByRole('region', { name: 'Приложения', exact: true })
      .getByRole('button', { name: 'diagram.pdf', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Развернуть код', exact: true }).click();
  await expect(page.locator('app-sidebar')).toBeHidden();
});

test('participant submission waits for a mixed source and attachment batch', async ({ page }) => {
  const state = await fixture(page, 'project');
  let release!: () => void;
  const blocked = new Promise<void>(resolve => {
    release = resolve;
  });
  let sourceStarted = false;
  state.beforeSource = async () => {
    sourceStarted = true;
    await blocked;
  };
  await expect(
    page.locator('app-remote-files').getByRole('button', { name: 'Загрузить файлы', exact: true }),
  ).toBeEnabled();
  await page.locator('app-remote-files input[type=file]').setInputFiles([
    { name: 'answer.py', mimeType: 'text/plain', buffer: Buffer.from('print(42)') },
    { name: 'diagram.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF\0bytes') },
  ]);
  await expect.poll(() => sourceStarted).toBe(true);
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Подтвердить отправку', exact: true })).toHaveCount(
    0,
  );
  release();
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect.poll(() => state.sentVersion).toBe(1);
  expect(state.source).toBe('print(42)');
  expect(state.files).toHaveLength(1);
});

test('ordinary completion saves a changed assessment and the exact attachment version together', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel.locator('input[type=file]').setInputFiles({
    name: 'example.sql',
    mimeType: 'text/plain',
    buffer: Buffer.from('SELECT 42;'),
  });
  await expect.poll(() => state.files.length).toBe(1);
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page
    .getByLabel('Сильные стороны', { exact: true })
    .fill('Верное решение. Пример приложен.');
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  await expect.poll(() => state.sentVersion).toBe(3);
  await expect(page).toHaveURL(/review-results\/review-one$/);
});

test('binary downloads retain exact bytes and oversized uploads never reach the API', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  const bytes = Buffer.from([0, 255, 128, 10, 1]);
  await panel
    .locator('input[type=file]')
    .setInputFiles({ name: 'data.bin', mimeType: 'application/octet-stream', buffer: bytes });
  await expect(panel.getByRole('button', { name: 'Скачать вложение', exact: true })).toBeVisible();
  const downloading = page.waitForEvent('download');
  await panel.getByRole('button', { name: 'Скачать вложение', exact: true }).click();
  const downloaded = await downloading;
  expect(downloaded.suggestedFilename()).toBe('data.bin');
  const stream = await downloaded.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  expect(Buffer.concat(chunks)).toEqual(bytes);
  await panel.locator('input[type=file]').setInputFiles({
    name: 'too-large.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.alloc(10485761),
  });
  await expect(panel.getByRole('alert')).toContainText('10 МиБ');
  expect(state.keys).toHaveLength(1);
});

test('sending waits for the complete multi-file upload batch', async ({ page }) => {
  const state = await fixture(page, 'review');
  let release!: () => void;
  const blocked = new Promise<void>(resolve => {
    release = resolve;
  });
  state.beforeUpload = async path => {
    if (path === 'second.bin') await blocked;
  };
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel.locator('input[type=file]').setInputFiles([
    { name: 'first.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 1]) },
    { name: 'second.bin', mimeType: 'application/octet-stream', buffer: Buffer.from([0, 2]) },
  ]);
  await expect.poll(() => state.keys.length).toBe(2);
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }),
  ).toHaveCount(0);
  expect(state.sentVersion).toBe(-1);
  release();
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  await expect.poll(() => state.sentVersion).toBe(3);
  expect(state.files.map(file => file.path)).toEqual(['first.bin', 'second.bin']);
});

test('refreshing feedback cannot rebase a conflicting attachment draft over another tab', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel.locator('input[type=file]').setInputFiles({
    name: 'example.sql',
    mimeType: 'text/plain',
    buffer: Buffer.from('SELECT 1;'),
  });
  const editor = panel.getByRole('textbox', { name: 'Редактор: example.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  state.files[0].bytes = Buffer.from('SELECT 99;');
  state.files[0].sha256 = 'external-sha';
  state.bumpVersion();
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 2;');
  await expect(panel.getByRole('alert')).toBeVisible();
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByRole('button', { name: 'Загрузить серверный черновик', exact: true }).click();
  await page.getByRole('button', { name: 'Заменить черновик', exact: true }).click();
  await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
  await expect(editor).toHaveText('SELECT 2;');
  await expect(panel.getByRole('alert')).toContainText('Файл изменился на сервере');
  await panel.getByRole('button', { name: 'Повторить сохранение приложений', exact: true }).click();
  expect(state.files[0].bytes.toString()).toBe('SELECT 99;');
  expect(state.sentVersion).toBe(-1);
});

test('a rejected binary upload survives reload and requires an explicit new name', async ({
  page,
}) => {
  const state = await fixture(page, 'review');
  state.rejectNext = true;
  const bytes = Buffer.from([0, 255, 13, 12]);
  const panel = page.getByRole('region', { name: 'Приложения к отзыву', exact: true });
  await panel
    .locator('input[type=file]')
    .setInputFiles({ name: 'report.bin', mimeType: 'application/octet-stream', buffer: bytes });
  await expect(
    panel.getByRole('button', { name: 'Повторить загрузку с другим именем', exact: true }),
  ).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
  await page.getByRole('button', { name: 'Приложения к отзыву', exact: true }).click();
  page.once('dialog', dialog => dialog.accept('report-new.bin'));
  await panel
    .getByRole('button', { name: 'Повторить загрузку с другим именем', exact: true })
    .click();
  await expect(panel.getByRole('button', { name: 'report-new.bin', exact: true })).toBeVisible();
  expect(state.files).toHaveLength(1);
  expect(state.files[0].bytes).toEqual(bytes);
  expect(state.keys[0]).not.toBe(state.keys[1]);
});

test('expert focus refresh retains the mounted file editor and selected group', async ({
  page,
}) => {
  await fixture(page, 'quality');
  const panel = page.getByRole('region', { name: 'Приложения к заключению', exact: true });
  await panel.locator('input[type=file]').setInputFiles({
    name: 'notes.sql',
    mimeType: 'text/plain',
    buffer: Buffer.from('SELECT 42;'),
  });
  const editor = panel.getByRole('textbox', { name: 'Редактор: notes.sql', exact: true });
  await expect(editor).toHaveText('SELECT 42;');
  await editor.evaluate(element => element.setAttribute('data-editor-identity', 'retained'));
  const refreshed = page.waitForResponse(response => response.url().endsWith('/source-review'));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await refreshed;
  await expect(editor).toHaveAttribute('data-editor-identity', 'retained');
  await expect(editor).toHaveText('SELECT 42;');
});
