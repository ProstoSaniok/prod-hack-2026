import { expect, test } from '@playwright/test';

// JSON allows paths with query strings and commas without ambiguous splitting.
const configuredPaths: unknown = JSON.parse(
  process.env['TEST_PATHS'] ?? '["/","/ui-kit","/sidebar"]',
);
if (
  !Array.isArray(configuredPaths) ||
  configuredPaths.length === 0 ||
  !configuredPaths.every(
    (path: unknown) => typeof path === 'string' && path.startsWith('/') && !path.startsWith('//'),
  )
) {
  throw new Error('TEST_PATHS must be a non-empty JSON array of relative paths starting with /');
}
const paths: string[] = configuredPaths;

for (const path of paths) {
  test.describe(path, () => {
    test('FE-A-01: страница доступна напрямую и после обновления', async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const response = await page.goto(path);
      expect(response?.ok(), 'Успешный HTTP-ответ страницы').toBe(true);
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('main')).not.toBeEmpty();
      const reloaded = await page.reload();
      expect(reloaded?.ok(), 'Успешный HTTP-ответ после обновления').toBe(true);
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('main')).not.toBeEmpty();
      expect(errors, 'Необработанные ошибки JavaScript при открытии и обновлении').toEqual([]);
    });

    test('FE-C-01: видимые элементы управления имеют доступные названия', async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('main')).toBeVisible();
      const controls = page
        .getByRole('button')
        .or(page.getByRole('link'))
        .or(page.getByRole('textbox'))
        .or(page.getByRole('combobox'))
        .or(page.getByRole('checkbox'))
        .or(page.getByRole('radio'))
        .or(page.getByRole('searchbox'))
        .or(page.getByRole('spinbutton'))
        .or(page.getByRole('slider'))
        .or(page.getByRole('switch'));
      const visible = controls.filter({ visible: true });
      test.skip((await visible.count()) === 0, 'На странице нет видимых элементов управления');
      for (const control of await visible.all()) {
        await expect.soft(control).toHaveAccessibleName(/\S/);
      }
    });

    test('FE-D-01: изображения в области просмотра загружаются', async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('main')).toBeVisible();
      const images = page.locator('img:visible');
      let checked = 0;
      for (const img of await images.all()) {
        // Offscreen lazy images need not load before the user scrolls to them.
        const inViewport = await img.evaluate(element => {
          const box = element.getBoundingClientRect();
          return box.bottom > 0 && box.right > 0 && box.top < innerHeight && box.left < innerWidth;
        });
        if (!inViewport) continue;
        checked++;
        await expect
          .poll(() =>
            img.evaluate(
              element =>
                (element as HTMLImageElement).complete &&
                (element as HTMLImageElement).naturalWidth > 0,
            ),
          )
          .toBe(true);
      }
      test.skip(checked === 0, 'В области просмотра нет изображений');
    });

    test('FE-E-01: страница не требует горизонтальной прокрутки', async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole('main')).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      const sizes = await page.evaluate(() => ({
        content: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
      }));
      expect(sizes.content).toBeLessThanOrEqual(sizes.viewport + 1);
    });
  });
}
