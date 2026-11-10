import { expect, test, type Page } from '@playwright/test';

const accent = 'rgb(255, 221, 45)';
const routes = [
  ['/', '/register', 'Регистрация'],
  ['/register', '/register', 'Регистрация'],
  ['/login', '/login', 'Вход'],
  ['/ui-kit', '/ui-kit', ''],
  ['/sidebar', '/sidebar', ''],
  ['/catalog', '/catalog', 'Каталог кейсов'],
  ['/my-cases', '/my-cases', 'В работе'],
  ['/projects/theme-project', '/projects/theme-project', 'Запросы SQL'],
  ['/cases/theme-case', '/cases/theme-case', 'Запросы SQL'],
  ['/admin', '/admin', 'Кейсы'],
] as const;

async function openRoute(
  page: Page,
  path: string,
  expectedPath = path,
  heading = '',
): Promise<void> {
  if (!['/', '/register', '/login', '/ui-kit', '/sidebar'].includes(path)) {
    await page.addInitScript(admin => {
      if (admin) sessionStorage.setItem('prodguild.admin.session.v1', 'theme-admin');
      else
        sessionStorage.setItem(
          'prodguild.api.session.v1',
          JSON.stringify({ token: 'theme-user', email: 'theme@example.com', name: 'Участник' }),
        );
    }, path.startsWith('/admin'));
    await page.route('**/auth/me', route =>
      route.fulfill({
        json: {
          id: 'theme-user',
          role: 'user',
          capabilities: [path.startsWith('/admin') ? 'organizer' : 'participant'],
        },
      }),
    );
    const study = {
      id: 'theme-case',
      title: 'Запросы SQL',
      description: 'Исправьте запрос.',
      expected_result: 'Работающий запрос',
      difficulty: 'review_minus',
      status: 'draft',
      content_version: 1,
      version: 1,
      created_at: '2026-10-17T10:00:00Z',
      frozen: false,
      competencies: [],
    };
    const project = {
      id: 'theme-project',
      case_id: study.id,
      case_version: 1,
      status: 'active',
      head_commit_sha: 'head-0',
    };
    const file = {
      path: 'solution.sql',
      sha: 'file-0',
      commit_sha: 'head-0',
      content: 'SELECT 1;',
    };
    await page.route('**/api/v1/**', route => {
      const current = new URL(route.request().url()).pathname;
      if (route.request().method() !== 'GET') return route.abort();
      if (current === '/api/v1/me/review-mode')
        return route.fulfill({
          json: {
            enabled: false,
            can_review: false,
            can_assess_readiness: false,
            status: 'disabled',
          },
        });
      if (current === '/api/v1/me/wallet')
        return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
      if (current === '/api/v1/me/projects') return route.fulfill({ json: [project] });
      if (current === '/api/v1/projects/theme-project') return route.fulfill({ json: project });
      if (current === '/api/v1/cases' || current === '/api/v1/admin/cases')
        return route.fulfill({ json: [study] });
      if (current === '/api/v1/cases/theme-case') return route.fulfill({ json: study });
      if (current.endsWith('/files/content')) return route.fulfill({ json: file });
      if (current.endsWith('/files')) return route.fulfill({ json: [file] });
      return route.fulfill({ json: [] });
    });
  }
  await page.goto(path);
  await expect(page).toHaveURL(new RegExp(`${expectedPath}$`));
  await expect(page.getByRole('main')).toBeVisible();
  if (heading)
    await expect(page.getByRole('heading', { name: heading, exact: true, level: 1 })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function gradientPaints(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const failures: string[] = [];
    for (const element of document.querySelectorAll('body *')) {
      const bounds = element.getBoundingClientRect();
      if (!bounds.width || !bounds.height) continue;
      for (const pseudo of [null, '::before', '::after']) {
        const style = getComputedStyle(element, pseudo);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (/gradient\(/i.test(style.backgroundImage)) {
          failures.push(
            `${element.tagName}.${element.getAttribute('class') ?? ''}${pseudo ?? ''}: ${style.backgroundImage}`,
          );
        }
      }
    }
    return [...new Set(failures)];
  });
}

function contrast(foreground: string, background: string): number {
  const luminance = (color: string): number => {
    const channels =
      color
        .match(/[\d.]+/g)
        ?.slice(0, 3)
        .map(Number) ?? [];
    if (channels.length !== 3) throw new Error(`Unsupported computed color: ${color}`);
    return channels.reduce((sum, channel, index) => {
      const value = channel / 255;
      const linear = value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      return sum + linear * [0.2126, 0.7152, 0.0722][index];
    }, 0);
  };
  const light = Math.max(luminance(foreground), luminance(background));
  const dark = Math.min(luminance(foreground), luminance(background));
  return (light + 0.05) / (dark + 0.05);
}

test.describe('flat brand theme', () => {
  test('shared tokens keep the approved flat palette', async ({ page }) => {
    await page.goto('/ui-kit');

    await expect
      .poll(() =>
        page.evaluate(() => {
          const root = getComputedStyle(document.documentElement);

          return [
            root.getPropertyValue('--prodguild-bg').trim(),
            root.getPropertyValue('--prodguild-surface').trim(),
            root.getPropertyValue('--prodguild-surface-raised').trim(),
            root.getPropertyValue('--prodguild-accent').trim(),
            root.getPropertyValue('--prodguild-text').trim(),
          ];
        }),
      )
      .toEqual(['#ededed', '#f9f9f9', '#fff', '#ffdd2d', '#3f3f3f']);
  });

  for (const [path, expectedPath, heading] of routes) {
    test(`${path}: visible controls and surfaces have no gradients`, async ({ page }) => {
      await openRoute(page, path, expectedPath, heading);

      const control = page
        .locator(
          'main button:visible, main input:visible, main select:visible, main textarea:visible, main a:visible',
        )
        .first();

      await expect(control, `Representative visible control on ${path}`).toBeVisible();
      expect(
        await gradientPaints(page),
        `${path}: all visible surfaces, including pseudo-elements`,
      ).toEqual([]);
    });
  }

  test('Taiga accent control stays exact yellow across interaction states', async ({ page }) => {
    await page.goto('/ui-kit');
    const button = page.getByRole('button', { name: 'Акцентная' });

    await button.scrollIntoViewIfNeeded();
    await expect(button).toHaveCSS('background-color', accent);
    await button.hover();
    await expect(button).toHaveCSS('background-color', accent);
    const box = await button.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(
      (box?.x ?? 0) + (box?.width ?? 0) / 2,
      (box?.y ?? 0) + (box?.height ?? 0) / 2,
    );
    await page.mouse.down();
    await expect(button).toHaveCSS('background-color', accent);
    await page.mouse.up();
  });

  test('rounded controls sit on contrasting nested surfaces', async ({ page }) => {
    await page.goto('/ui-kit');
    const panel = page.locator('.demo-panel').first();
    const input = page.getByRole('searchbox', { name: 'Поиск' });
    const button = page.getByRole('button', { name: 'Акцентная' });

    await expect(input).toBeVisible();
    await expect(button).toHaveCSS('border-radius', '16px');

    const backgrounds = await Promise.all([
      panel.evaluate(element => getComputedStyle(element).backgroundColor),
      input.evaluate(element => getComputedStyle(element).backgroundColor),
    ]);

    expect(backgrounds[0]).not.toBe(backgrounds[1]);
  });

  test('editor search controls remain flat and code retains a monospace font', async ({ page }) => {
    await openRoute(page, '/projects/theme-project');
    const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
    await expect(editor).toBeVisible();
    const scroller = page.locator('.cm-scroller');
    await expect(scroller).toHaveCSS('font-family', /monospace/);
    await editor.press('ControlOrMeta+f');
    await expect(page.getByRole('textbox', { name: 'Найти', exact: true })).toBeVisible();
    expect(await gradientPaints(page)).toEqual([]);
    const next = page.getByRole('button', { name: 'Далее', exact: true });
    await next.hover();
    await page.mouse.down();
    try {
      await expect(next).toHaveCSS('background-image', 'none');
      expect(await gradientPaints(page)).toEqual([]);
    } finally {
      await page.mouse.up();
    }
    await page.keyboard.press('Escape');
  });

  test('small sidebar retains readable contrast', async ({ page, viewport }) => {
    await openRoute(page, '/admin', '/admin', 'Кейсы');
    const sidebar = page.locator('app-sidebar');
    if (viewport && viewport.width <= 620)
      await sidebar.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    const label = sidebar.locator('.workspace-name');
    await expect(label).toBeVisible();
    const colors = await label.evaluate(element => ({
      foreground: getComputedStyle(element).color,
      background: getComputedStyle(element.closest('.sidebar') ?? element).backgroundColor,
    }));
    expect(contrast(colors.foreground, colors.background)).toBeGreaterThanOrEqual(4.5);
  });
});
