import { expect, Page, test } from '@playwright/test';

type Workspace = 'participant' | 'materials' | 'reference' | 'submission' | 'review' | 'quality';

async function fixture(page: Page, workspace: Workspace) {
  const organizer = workspace === 'materials' || workspace === 'reference';
  const snapshot = workspace === 'submission' || workspace === 'review' || workspace === 'quality';
  const state = { writes: 0, conflict: false, beforeWrite: async (): Promise<void> => {} };
  const study = {
    id: 'maximize-case',
    title: 'Запросы SQL',
    description: 'Исправьте запросы.',
    expected_result: 'Работающий запрос',
    difficulty: 'review_minus',
    content_version: 1,
    version: 1,
    status: 'draft',
    frozen: false,
    competencies: [],
    created_at: '2026-10-17T10:00:00Z',
  };
  const project = {
    id: 'maximize-project',
    case_id: study.id,
    case_version: 1,
    status: 'active',
    head_commit_sha: 'head-0',
  };
  const assignment = {
    id: 'maximize-review',
    submission_id: 'maximize-submission',
    kind: workspace === 'quality' ? 'quality_check' : 'review',
    review_id: null,
    revision_id: null,
    status: 'assigned',
    lease_expires_at: '2030-10-18T12:00:00Z',
  };
  const files = Array.from({ length: 40 }, (_, index) => ({
    path: `file${String(index).padStart(2, '0')}.sql`,
    sha: `file-${index}-v1`,
    commit_sha: 'head-0',
    content: index ? 'SELECT 2;' : Array.from({ length: 500 }, (_, i) => `SELECT ${i};`).join('\n'),
  }));
  await page.addInitScript(admin => {
    if (admin) sessionStorage.setItem('prodguild.admin.session.v1', 'maximize-admin');
    else
      sessionStorage.setItem(
        'prodguild.api.session.v1',
        JSON.stringify({
          token: 'maximize-user',
          email: 'maximize@example.com',
          name: 'Участник',
        }),
      );
  }, organizer);
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'maximize-user',
        role: 'user',
        capabilities: organizer ? ['organizer'] : [],
      },
    }),
  );
  await page.route('**/api/v1/**', async route => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    if (path.endsWith('/me/review-mode'))
      return route.fulfill({
        json: {
          can_review: workspace === 'review',
          can_quality_review: workspace === 'quality',
          enabled: workspace === 'review' || workspace === 'quality',
          status: 'available',
        },
      });
    if (path.endsWith('attachments'))
      return route.fulfill({
        json: {
          version: path.includes('/projects/') ? 0 : 1,
          items: [],
          total_bytes: 0,
          max_file_bytes: 10 * 1024 * 1024,
          max_total_bytes: 50 * 1024 * 1024,
          max_files: 20,
        },
      });
    if (path.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, available: 10, reserved: 0 } });
    if (path === '/api/v1/admin/cases' || path === '/api/v1/cases')
      return route.fulfill({ json: [study] });
    if (path === '/api/v1/cases/maximize-case' || path === '/api/v1/admin/cases/maximize-case')
      return route.fulfill({ json: study });
    if (path === '/api/v1/projects/maximize-project') return route.fulfill({ json: project });
    if (path.endsWith('/reference-solution'))
      return route.fulfill({
        json: {
          case_id: study.id,
          version: 1,
          ready: false,
          head_commit_sha: 'head-0',
        },
      });
    if (path === '/api/v1/review-assignments/maximize-review')
      return route.fulfill({ json: assignment });
    if (path.endsWith('/source-review'))
      return route.fulfill({
        json: {
          id: 'source-review',
          assignment_id: 'source-assignment',
          reviewer_id: 'reviewer',
          submission_id: 'maximize-submission',
          status: 'pending_quality_check',
          current_revision: {
            id: 'source-revision',
            score: 85,
            verdict: 'approved',
            feedback: { strengths: 'Работает', problems: 'Нет', next_steps: 'Проверить SQL' },
            comments: [],
          },
          workspace: {
            assignment,
            case: study,
            draft: {
              version: 1,
              score: null,
              verdict: null,
              feedback: { strengths: '', problems: '', next_steps: '' },
              comments: [],
            },
          },
        },
      });
    if (path.endsWith('/workspace'))
      return route.fulfill({
        json: {
          assignment,
          case: study,
          draft: {
            version: 1,
            score: null,
            verdict: null,
            feedback: { strengths: '', problems: '', next_steps: '' },
            comments: [],
          },
        },
      });
    if (path === '/api/v1/submissions/maximize-submission')
      return route.fulfill({
        json: {
          id: 'maximize-submission',
          project_id: project.id,
          case_id: study.id,
          case_version: 1,
          status: 'submitted',
          submitted_at: '2026-10-17T10:00:00Z',
          commit_sha: 'head-0',
          base_commit_sha: 'base-0',
        },
      });
    if (path.endsWith('/files/content')) {
      const file = files.find(item => item.path === url.searchParams.get('path'));
      if (request.method() === 'PUT') {
        state.writes++;
        await state.beforeWrite();
        if (state.conflict) return route.fulfill({ status: 409, json: { code: 'FILE_CHANGED' } });
        if (file) {
          file.content = request.postDataJSON().content;
          file.sha = `saved-${state.writes}`;
        }
      }
      return route.fulfill({
        json: {
          ...file,
          content:
            snapshot && url.searchParams.get('side') === 'base' ? 'SELECT 0;' : file?.content,
        },
      });
    }
    if (path.endsWith('/files'))
      return route.fulfill({ json: snapshot ? { entries: files, next_offset: null } : files });
    if (path.endsWith('/diff')) return route.fulfill({ json: { entries: [], next_offset: null } });
    if (path.endsWith('/reviews') || path.endsWith('/quality-assessments'))
      return route.fulfill({ json: { items: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
  const routes: Record<Workspace, string> = {
    participant: '/projects/maximize-project',
    materials: '/admin/maximize-case/edit?section=materials',
    reference: '/admin/maximize-case/edit?section=reference',
    submission: '/submissions/maximize-submission',
    review: '/reviews/maximize-review',
    quality: '/expert/maximize-review',
  };
  await page.goto(routes[workspace]);
  if (workspace === 'review') {
    await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
    await page.getByRole('button', { name: 'Все файлы', exact: true }).click();
  }
  if (snapshot)
    await page
      .getByRole('navigation', { name: 'Файлы решения', exact: true })
      .getByRole('button', { name: 'file00.sql', exact: true })
      .click();
  await expect(editor(page)).toContainText('SELECT 0;');
  return state;
}

function editor(page: Page) {
  return page.getByRole('textbox', { name: 'Редактор: file00.sql', exact: true });
}

async function expand(page: Page) {
  await editor(page).evaluate(element => element.setAttribute('data-editor-identity', 'original'));
  await page.getByRole('button', { name: 'Развернуть код', exact: true }).first().click();
  const target = page.locator('[data-code-maximize-target]');
  await expect(target).toBeVisible();
  await expect.poll(() => target.evaluate(element => element.getAnimations().length)).toBe(0);
  await expect(editor(page)).toHaveAttribute('data-editor-identity', 'original');
  return target;
}

for (const workspace of [
  'participant',
  'materials',
  'reference',
  'submission',
  'review',
  'quality',
] as const) {
  test(`${workspace}: fills the whole app and preserves editor and sidebar state`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await fixture(page, workspace);
    const target = await expand(page);
    const viewport = page.viewportSize();
    if (!viewport) throw new Error('Viewport is required');
    await expect.poll(async () => Math.round((await target.boundingBox())?.x ?? -1)).toBe(0);
    await expect
      .poll(async () => Math.round((await target.boundingBox())?.height ?? -1))
      .toBe(viewport.height);
    const rect = await target.boundingBox();
    if (!rect) throw new Error('Expanded workspace is visible');
    expect(rect.y).toBe(0);
    expect(rect.height).toBe(viewport.height);
    expect(Math.round(rect.x + rect.width)).toBe(viewport.width);
    await expect(page.locator('h1')).toBeHidden();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
    ).toBeLessThanOrEqual(1);
    const shell = page.locator('[data-workspace-host]');
    await expect(page.locator('app-sidebar')).toBeHidden();
    await page.screenshot({ path: info.outputPath(`${workspace}-expanded.png`) });
    const collapse = page.getByRole('button', { name: 'Свернуть код', exact: true });
    await expect(collapse).toBeInViewport({ ratio: 1 });
    await collapse.focus();
    await page.keyboard.press('Escape');
    await expect(shell).not.toHaveClass(/code-maximized/);
    await expect(
      page.getByRole('button', { name: 'Развернуть код', exact: true }).first(),
    ).toBeFocused();
    await expect(editor(page)).toHaveAttribute('data-editor-identity', 'original');
    await expect(page.locator('h1')).toBeVisible();
    if (info.project.name === 'mobile')
      await expect(
        page.getByRole('button', { name: 'Показать боковую панель', exact: true }),
      ).toBeVisible();
    else await expect(page.locator('app-sidebar')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.style.overflow)).toBe('');
    expect(errors).toEqual([]);
  });
}

test('typing, undo, an in-flight save and breakpoint changes preserve the same editor', async ({
  page,
}) => {
  const state = await fixture(page, 'participant');
  const field = editor(page);
  await field.click();
  await field.press('ControlOrMeta+A');
  await field.pressSequentially('SELECT 42;');
  await expand(page);
  await field.press('ControlOrMeta+z');
  await expect(field).toContainText('SELECT 499;');
  await field.press('ControlOrMeta+A');
  await field.pressSequentially('SELECT 42;');
  let release: () => void = () => {};
  state.beforeWrite = () =>
    new Promise<void>(resolve => {
      release = resolve;
    });
  await field.press('ControlOrMeta+s');
  await expect.poll(() => state.writes).toBe(1);
  await field.press('End');
  await field.pressSequentially(' -- draft');
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(field).toHaveAttribute('data-editor-identity', 'original');
  await expect(field).toHaveText('SELECT 42; -- draft');
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).click();
  await expect(field).toHaveAttribute('data-editor-identity', 'original');
  await expect(field).toHaveText('SELECT 42; -- draft');
  state.beforeWrite = async () => {};
  release();
  await expect(page.getByText('Сохранено', { exact: true })).toBeVisible();
});

test('a rejected browser navigation retains expanded code and dirty input', async ({
  page,
}, info) => {
  const state = await fixture(page, 'participant');
  if (info.project.name === 'mobile')
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await page.getByRole('link', { name: 'Каталог кейсов', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goBack();
  await expect(editor(page)).toBeVisible();
  state.conflict = true;
  await editor(page).click();
  await editor(page).press('ControlOrMeta+A');
  await editor(page).pressSequentially('SELECT 42;');
  await expand(page);
  await editor(page).press('ControlOrMeta+s');
  await expect(page.getByRole('alert').first()).toBeVisible();
  page.once('dialog', dialog => dialog.dismiss());
  await page.goForward();
  await expect(page).toHaveURL(/\/projects\/maximize-project$/);
  await expect(page.locator('[data-workspace-host]')).toHaveClass(/code-maximized/);
  await expect(editor(page)).toHaveText('SELECT 42;');
});

test('restores page position and preserves editor and tree scroll when collapsing', async ({
  page,
}) => {
  await fixture(page, 'participant');
  const code = page.locator('app-remote-files .cm-scroller');
  const tree = page.locator('app-remote-files .embedded-explorer');
  // Measure the visible editor before scrolling, as a user would: CodeMirror
  // estimates offscreen line heights until the editor enters the viewport.
  await editor(page).click();
  await code.evaluate(element => (element.scrollTop = 800));
  await tree.evaluate(element => (element.scrollTop = 100));
  await page
    .getByRole('button', { name: 'Развернуть код', exact: true })
    .first()
    .scrollIntoViewIfNeeded();
  await expect.poll(() => code.evaluate(element => element.scrollTop)).toBe(800);
  const before = await page.evaluate(() => window.scrollY);
  await expand(page);
  await expect.poll(() => code.evaluate(element => element.scrollTop)).toBe(800);
  await expect.poll(() => tree.evaluate(element => element.scrollTop)).toBe(100);
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(before);
  await expect.poll(() => code.evaluate(element => element.scrollTop)).toBe(800);
  await expect.poll(() => tree.evaluate(element => element.scrollTop)).toBe(100);
});

test('snapshot files and source version remain usable and read-only while expanded', async ({
  page,
}) => {
  const state = await fixture(page, 'submission');
  const target = await expand(page);
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  await target.getByRole('button', { name: 'Исходные файлы', exact: true }).click();
  const files = target.getByRole('navigation', { name: 'Файлы решения', exact: true });
  await files.getByRole('button', { name: 'file00.sql', exact: true }).click();
  await expect(editor(page)).toHaveText('SELECT 0;');
  await target.getByRole('button', { name: 'Решение', exact: true }).click();
  await files.getByRole('button', { name: 'file01.sql', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Редактор: file01.sql', exact: true })).toHaveText(
    'SELECT 2;',
  );
  await expect(target.getByRole('button', { name: 'Удалить', exact: true })).toHaveCount(0);
  await expect(target.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  expect(state.writes).toBe(0);
});

test('reduced motion expands immediately without animating the editor', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await fixture(page, 'participant');
  const target = await expand(page);
  expect(await target.evaluate(element => element.getAnimations().length)).toBe(0);
  await expect(page.locator('app-sidebar')).toBeHidden();
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).click();
  await expect(editor(page)).toHaveAttribute('data-editor-identity', 'original');
});

test('successful browser navigation releases fullscreen and page scroll locks', async ({
  page,
}, info) => {
  await fixture(page, 'participant');
  if (info.project.name === 'mobile')
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await page.getByRole('link', { name: 'Каталог кейсов', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goBack();
  await expect(editor(page)).toBeVisible();
  await expand(page);
  await page.goForward();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.locator('[data-code-maximize-target]')).toHaveCount(0);
  await expect(page.locator('[data-workspace-host]')).not.toHaveClass(/code-maximized/);
  expect(await page.evaluate(() => document.documentElement.style.overflow)).toBe('');
});

test('expert fullscreen keeps source review, attachments and assessment draft reachable', async ({
  page,
}) => {
  await fixture(page, 'quality');
  await expand(page);
  const target = page.locator('[data-code-maximize-target]');
  const feedback = page.getByRole('textbox', { name: 'Комментарий эксперта', exact: true });
  await feedback.fill('Содержательный черновик проверки');
  await target.getByRole('button', { name: 'Исходное ревью', exact: true }).click();
  await expect(target.getByRole('region', { name: 'Исходное ревью', exact: true })).toBeVisible();
  await expect(
    target.getByRole('heading', { name: 'Приложения к исходному ревью', exact: true }),
  ).toBeVisible();
  await target.getByRole('button', { name: 'Приложения к решению', exact: true }).click();
  await expect(
    target.getByRole('heading', { name: 'Приложения к решению', exact: true }),
  ).toBeVisible();
  await target.getByRole('button', { name: 'Приложения к заключению', exact: true }).click();
  await expect(target.getByRole('button', { name: 'Загрузить файлы', exact: true })).toBeVisible();
  await target.getByRole('button', { name: 'Файлы решения', exact: true }).click();
  await expect(editor(page)).toHaveAttribute('data-editor-identity', 'original');
  await expect(feedback).toHaveValue('Содержательный черновик проверки');
  await expect(
    target.getByRole('button', { name: 'Сохранить экспертное решение', exact: true }),
  ).toBeEnabled();
});

test('repeated toggles and resizing finish current motion without recreating the editor', async ({
  page,
}) => {
  await fixture(page, 'participant');
  const target = await expand(page);
  const original = await target.elementHandle();
  if (!original) throw new Error('Expanded target is required');
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).dispatchEvent('click');
  await page
    .getByRole('button', { name: 'Развернуть код', exact: true })
    .first()
    .dispatchEvent('click');
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect.poll(() => original.evaluate(element => element.getAnimations().length)).toBe(0);
  await expect(target).toBeVisible();
  await expect(editor(page)).toHaveAttribute('data-editor-identity', 'original');
  await expect.poll(async () => Math.round((await target.boundingBox())?.width ?? -1)).toBe(768);
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).dispatchEvent('click');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => original.evaluate(element => element.getAnimations().length)).toBe(0);
  await expect(page.locator('[data-workspace-host]')).not.toHaveClass(/code-maximized/);
  expect(await page.evaluate(() => document.documentElement.style.overflow)).toBe('');
});

test('navigation during closing cancels outgoing motion and releases layout', async ({
  page,
}, info) => {
  await fixture(page, 'participant');
  if (info.project.name === 'mobile')
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await page.getByRole('link', { name: 'Каталог кейсов', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goBack();
  await expect(editor(page)).toBeVisible();
  const target = await expand(page);
  const original = await target.elementHandle();
  if (!original) throw new Error('Expanded target is required');
  await page.getByRole('button', { name: 'Свернуть код', exact: true }).dispatchEvent('click');
  await page.goForward();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.locator('[data-code-maximize-target]')).toHaveCount(0);
  expect(await original.evaluate(element => element.getAnimations().length)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.style.overflow)).toBe('');
});
