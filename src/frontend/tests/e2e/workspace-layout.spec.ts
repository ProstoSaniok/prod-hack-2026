import { expect, Locator, Page, test } from '@playwright/test';

type Workspace = 'participant' | 'organizer';
const project = {
  id: 'layout-project',
  case_id: 'layout-case',
  case_version: 1,
  status: 'active',
  head_commit_sha: 'head-0',
};
const study = {
  id: 'layout-case',
  title: 'Запросы SQL',
  description: 'Исправьте запросы.',
  expected_result: 'Работающие запросы',
  difficulty: 'review_minus',
  content_version: 1,
  version: 1,
  created_at: '2026-10-17T10:00:00Z',
  frozen: false,
  status: 'draft',
  competencies: [],
};

async function fixture(page: Page, workspace: Workspace): Promise<void> {
  await page.addInitScript(role => {
    if (role === 'organizer') sessionStorage.setItem('prodguild.admin.session.v1', 'layout-admin');
    else
      sessionStorage.setItem(
        'prodguild.api.session.v1',
        JSON.stringify({ token: 'layout-user', email: 'layout@example.com', name: 'Участник' }),
      );
  }, workspace);
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'layout-user',
        role: 'user',
        capabilities: workspace === 'organizer' ? ['organizer'] : [],
      },
    }),
  );
  const files = Array.from({ length: 60 }, (_, index) => ({
    path: `file${String(index).padStart(2, '0')}.sql`,
    sha: `file-${index}-v1`,
    commit_sha: 'head-0',
    content: index
      ? 'SELECT 1;'
      : Array.from({ length: 1000 }, (_, i) => `SELECT ${i};`).join('\n'),
  }));
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/v1/me/projects') return route.fulfill({ json: project });
    if (route.request().method() !== 'GET') return route.abort();
    if (path === '/api/v1/projects/layout-project') return route.fulfill({ json: project });
    if (path === '/api/v1/cases/layout-case' || path === '/api/v1/admin/cases/layout-case')
      return route.fulfill({ json: study });
    if (path.endsWith('/files/content'))
      return route.fulfill({
        json: files.find(file => file.path === url.searchParams.get('path')),
      });
    if (path.endsWith('/files')) return route.fulfill({ json: files });
    if (path.endsWith('/reference-solution'))
      return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
    if (path.endsWith('/me/review-mode'))
      return route.fulfill({ json: { enabled: false, can_review: false, status: 'disabled' } });
    if (path.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    return route.fulfill({ json: [] });
  });
  await page.goto(
    workspace === 'participant'
      ? '/projects/layout-project'
      : '/admin/layout-case/edit?section=materials',
  );
  await expect(
    page.getByRole('textbox', { name: 'Редактор: file00.sql', exact: true }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function geometry(locator: Locator) {
  return locator.evaluate(element => {
    const box = element.getBoundingClientRect();
    return {
      top: box.top,
      right: box.right,
      bottom: box.bottom,
      x: box.x,
      width: box.width,
      height: box.height,
      scrollHeight: element.scrollHeight,
      clientHeight: element.clientHeight,
    };
  });
}

async function pageHeight(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollHeight);
}

async function expectBoundedEditor(page: Page): Promise<void> {
  const outer = await geometry(page.locator('.files-editor'));
  const repository = await geometry(page.locator('.repository-browser'));
  const code = await geometry(page.locator('.cm-scroller'));
  const footer = await geometry(page.locator('.editor-footer'));
  expect(repository.height).toBeLessThanOrEqual(outer.height + 1);
  expect(repository.scrollHeight).toBeLessThanOrEqual(repository.clientHeight + 1);
  expect(code.height).toBeGreaterThan(100);
  expect(code.bottom).toBeLessThanOrEqual(footer.top + 1);
  expect(footer.bottom).toBeLessThanOrEqual(outer.bottom + 1);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
}

async function expectAttachedToggle(page: Page): Promise<void> {
  const panel = await geometry(page.locator('app-sidebar .sidebar'));
  const toggle = await geometry(page.locator('app-sidebar .edge-toggle').first());
  expect(Math.abs(toggle.right - panel.right - 18)).toBeLessThanOrEqual(1);
  expect(
    Math.abs(toggle.top + toggle.height / 2 - panel.top - panel.height / 2),
  ).toBeLessThanOrEqual(1);
}

for (const workspace of ['participant', 'organizer'] as const) {
  test(`${workspace}: long code and file trees scroll inside a bounded ordinary page`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await fixture(page, workspace);
    const height = await pageHeight(page);
    await expectBoundedEditor(page);
    const scroller = page.locator('.cm-scroller');
    expect((await geometry(scroller)).scrollHeight).toBeGreaterThan(10000);
    const pageScroll = await page.evaluate(() => window.scrollY);
    await scroller.evaluate(element => (element.scrollTop = element.scrollHeight));
    await expect(
      page.getByRole('textbox', { name: 'Редактор: file00.sql', exact: true }),
    ).toContainText('SELECT 999;');
    expect(await page.evaluate(() => window.scrollY)).toBe(pageScroll);
    expect(await pageHeight(page)).toBe(height);
    const tree = page.locator('.embedded-explorer');
    const lastFile = tree.getByRole('button', { name: 'file59.sql', exact: true });
    await tree.evaluate(element => (element.scrollTop = element.scrollHeight));
    await lastFile.click();
    await expect(
      page.getByRole('textbox', { name: 'Редактор: file59.sql', exact: true }),
    ).toHaveText('SELECT 1;');
    await expect.poll(() => pageHeight(page)).toBe(height);
    await expectBoundedEditor(page);
    await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
    const name = page.locator('app-sidebar .workspace-name');
    if (workspace === 'participant') await expect(name).toHaveCount(0);
    else await expect(name).toHaveText('Администрирование');
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.screenshot({ path: info.outputPath(`${workspace}-bounded-editor.png`) });
    expect(errors).toEqual([]);
  });

  test(`${workspace}: sidebar controls stay aligned during page scroll`, async ({ page }, info) => {
    await fixture(page, workspace);
    const mobile = info.project.name === 'mobile';
    if (!mobile) {
      await page.setViewportSize({ width: page.viewportSize()?.width ?? 1440, height: 600 });
      for (const expanded of [true, false, true]) {
        const sidebar = page.locator('app-sidebar');
        if ((await sidebar.getAttribute('data-expanded')) !== String(expanded))
          await page.locator('app-sidebar .edge-toggle').first().click();
        await expect(sidebar).toHaveAttribute('data-expanded', String(expanded));
        await expect.poll(async () => (await geometry(sidebar)).width).toBe(expanded ? 280 : 88);
        for (const position of ['top', 'bottom'] as const) {
          await page.evaluate(
            where =>
              window.scrollTo(0, where === 'top' ? 0 : document.documentElement.scrollHeight),
            position,
          );
          await expectAttachedToggle(page);
          await expect(page.locator('app-sidebar .sidebar')).toBeInViewport();
          await expectBoundedEditor(page);
        }
      }
    } else {
      const toggle = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
      await page.evaluate(() => window.scrollTo(0, 0));
      const opener = await geometry(toggle);
      if (workspace === 'participant') {
        const heading = await geometry(page.locator('.project-heading h1'));
        expect(heading.x).toBeGreaterThanOrEqual(opener.right);
      }
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const scroll = await page.evaluate(() => window.scrollY);
      await toggle.click();
      const dialog = page.getByRole('dialog', { name: 'Навигация', exact: true });
      await expect(dialog).toBeVisible();
      await expect(
        page.locator(workspace === 'participant' ? '.case-content' : '.admin-content'),
      ).toHaveJSProperty('inert', true);
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(toggle).toBeFocused();
      expect(await page.evaluate(() => window.scrollY)).toBe(scroll);
      expect((await geometry(toggle)).top).toBe(opener.top);
    }
  });
}

test('both project entry routes use the same ordinary workspace layout', async ({ page }) => {
  await fixture(page, 'participant');
  const main = page.locator('#case-main');
  await expect(main).toHaveClass('workspace-main');
  const direct = await geometry(main);
  await page.goto('/cases/layout-case/work');
  await expect(
    page.getByRole('textbox', { name: 'Редактор: file00.sql', exact: true }),
  ).toBeVisible();
  await expect(main).toHaveClass('workspace-main');
  expect(await geometry(main)).toEqual(direct);
});

test('desktop toggle follows its panel when a consumer ends before the page', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'The mobile drawer is anchored to the viewport');
  await page.goto('/sidebar');
  await expect(page.locator('app-sidebar .workspace-name')).toHaveText('Продуктовая команда');
  await page.addStyleTag({
    content:
      'app-sidebar {height:calc(100dvh + 100px);align-self:flex-start} main {min-height:300dvh}',
  });
  await expectAttachedToggle(page);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expectAttachedToggle(page);
  await expect(page.locator('app-sidebar .sidebar')).not.toBeInViewport();
  await expect(page.locator('app-sidebar .edge-toggle').first()).not.toBeInViewport();
});

test('sidebar animation preserves the editor DOM, draft and undo history', async ({
  page,
}, info) => {
  await fixture(page, 'organizer');
  const tree = page.locator('.embedded-explorer');
  await tree.evaluate(element => (element.scrollTop = element.scrollHeight));
  await tree.getByRole('button', { name: 'file59.sql', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: file59.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.evaluate(element => (element.dataset['motionIdentity'] = 'original'));
  await editor.click();
  await editor.press('ControlOrMeta+End');
  await editor.pressSequentially('\nSELECT 2;');
  await expect(editor).toContainText('SELECT 2;');
  if (info.project.name === 'mobile') {
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.sidebar-frame')).toHaveCSS('visibility', 'hidden');
  } else {
    const sidebar = page.locator('app-sidebar');
    for (const [label, width] of [
      ['Скрыть боковую панель', 88],
      ['Показать боковую панель', 280],
    ] as const) {
      await page.getByRole('button', { name: label, exact: true }).click();
      await expect.poll(async () => (await geometry(sidebar)).width).toBe(width);
    }
  }
  await expect(editor).toHaveAttribute('data-motion-identity', 'original');
  await expect(editor).toContainText('SELECT 2;');
  await editor.click();
  await editor.press('ControlOrMeta+z');
  await expect(editor).toHaveText('SELECT 1;');
  await expectBoundedEditor(page);
});
