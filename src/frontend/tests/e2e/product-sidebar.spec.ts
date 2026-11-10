import { expect, Page, test } from '@playwright/test';
import { authLayoutFixture, caseId, openFixtureProject } from './fixtures/auth-layout';

const showLabel = 'Показать боковую панель';
const hideLabel = 'Скрыть боковую панель';
const solution =
  'Проверяю влияние первого перевода на удержание. Сравниваю две версии сценария и измеряю возврат клиентов через месяц.';

async function expectUnlocked(page: Page): Promise<void> {
  await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toHaveCount(0);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
}

async function expectNoOverflow(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
}

test('стрелка сворачивает панель до иконок и возвращает подписи без потери решения и баланса', async ({
  page,
  viewport,
}) => {
  const state = await authLayoutFixture(page);
  await openFixtureProject(page);
  const mobile = Boolean(viewport && viewport.width <= 620);
  const draft = page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true });
  const balance = page.getByTestId('credits-balance');
  await draft.fill(solution);
  await expect.poll(() => state.file.content).toBe(solution);
  await expect(balance).toHaveAttribute('aria-label', 'Баланс: 37 PRODитов. История операций');
  const originalWidth = await page
    .locator('.case-content')
    .evaluate(element => element.getBoundingClientRect().width);
  if (mobile) await page.getByRole('button', { name: showLabel, exact: true }).click();
  await page.getByRole('button', { name: hideLabel, exact: true }).click();
  const navigation = page.getByRole('navigation', { name: 'Основная навигация', exact: true });
  if (mobile) {
    await expect(navigation).toHaveCount(0);
  } else {
    await expect(navigation).toBeVisible();
    await expect(page.locator('app-sidebar')).toHaveCSS('width', '88px');
    await expect(page.locator('app-sidebar .item-label').first()).toHaveCSS('opacity', '0');
    await expect(page.getByRole('link', { name: 'Каталог кейсов', exact: true })).toBeVisible();
    await expect(page.locator('app-sidebar .toggle-chevron path')).toHaveAttribute(
      'd',
      'm10 6 6 6-6 6',
    );
  }
  await expect(page.getByRole('button', { name: showLabel, exact: true })).toBeVisible();
  await expectUnlocked(page);
  if (!mobile) {
    await expect
      .poll(() =>
        page.locator('.case-content').evaluate(element => element.getBoundingClientRect().width),
      )
      .toBeCloseTo(originalWidth + 192, 0);
  }
  await expect(draft).toHaveText(solution);
  await expect(page.getByRole('list', { name: 'Этапы работы' })).toBeVisible();
  await expect(balance).toHaveAttribute('aria-label', 'Баланс: 37 PRODитов. История операций');
  await page.getByRole('button', { name: showLabel, exact: true }).click();
  await expect(
    page.getByRole('navigation', { name: 'Основная навигация', exact: true }),
  ).toBeVisible();
  if (mobile) await page.getByRole('button', { name: hideLabel, exact: true }).click();
  else {
    await expect(page.locator('app-sidebar')).toHaveCSS('width', '280px');
    await expect(page.locator('app-sidebar .item-label').first()).toBeVisible();
    await expect(page.locator('app-sidebar .item-label').first()).toHaveCSS('opacity', '1');
    await expect(page.locator('app-sidebar .toggle-chevron path')).toHaveAttribute(
      'd',
      'm14 6-6 6 6 6',
    );
  }
  await expect
    .poll(() =>
      page.locator('.case-content').evaluate(element => element.getBoundingClientRect().width),
    )
    .toBeCloseTo(originalWidth, 0);
  await expect(draft).toHaveText(solution);
  await expect(page.getByRole('list', { name: 'Этапы работы' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/лотти|lotty/i);
  expect(state.unexpected).toEqual([]);
  await expectNoOverflow(page);
});

test('наведение и фокус не раскрывают панель, ссылки свёрнутой полосы доступны с клавиатуры', async ({
  page,
  viewport,
}) => {
  const state = await authLayoutFixture(page);
  await page.goto('/catalog');
  if (viewport && viewport.width > 620) {
    await page.getByRole('button', { name: hideLabel, exact: true }).click();
  }
  const show = page.getByRole('button', { name: showLabel, exact: true });
  await show.hover();
  await show.focus();
  await expect(show).toHaveAttribute('aria-expanded', 'false');
  if (viewport && viewport.width > 620) {
    const catalog = page.getByRole('link', { name: 'Каталог кейсов', exact: true });
    await page.keyboard.press('Tab');
    await expect(
      page.getByRole('link', { name: 'prodguild — каталог кейсов', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(catalog).toBeFocused();
    await expect(page.locator('app-sidebar')).toHaveCSS('width', '88px');
    await expect(catalog).toHaveAttribute('aria-current', 'page');
    await expect(catalog).toHaveCSS('background-color', 'rgb(255, 221, 45)');
    await expect(catalog).toHaveCSS('outline-style', 'solid');
    await expect(
      page.locator('app-sidebar').getByRole('link', { name: 'Войти', exact: true }),
    ).toBeVisible();
  } else {
    await expect(
      page.getByRole('navigation', { name: 'Основная навигация', exact: true }),
    ).toHaveCount(0);
    for (let step = 0; step < 12; step++) {
      await page.keyboard.press('Tab');
      expect(
        await page.evaluate(() =>
          Boolean(document.activeElement?.closest('app-sidebar nav, app-sidebar .sidebar-footer')),
        ),
      ).toBe(false);
      await expect(show).toHaveAttribute('aria-expanded', 'false');
    }
  }
  await show.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('navigation', { name: 'Основная навигация', exact: true }),
  ).toBeVisible();
  expect(state.unexpected).toEqual([]);
});

test('мобильная панель удерживает фокус, закрывается Escape, фоном и переходом, очищает блокировки при ресайзе', async ({
  page,
  viewport,
}) => {
  const state = await authLayoutFixture(page);
  await openFixtureProject(page);
  const originalViewport = viewport ?? { width: 1440, height: 900 };
  if (originalViewport.width > 620) await page.setViewportSize({ width: 375, height: 812 });
  const show = page.getByRole('button', { name: showLabel, exact: true });
  const dialog = page.getByRole('dialog', { name: 'Навигация', exact: true });
  await show.focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');
  for (let step = 0; step < 10; step++) {
    await page.keyboard.press('Tab');
    expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press('Escape');
  await expectUnlocked(page);
  await expect(show).toBeFocused();
  await show.click();
  const mobileWidth = page.viewportSize()?.width ?? 375;
  await page
    .getByRole('button', { name: 'Закрыть меню', exact: true })
    .click({ position: { x: mobileWidth - 8, y: 200 } });
  await expectUnlocked(page);
  await expect(show).toBeFocused();
  await show.click();
  await dialog.getByRole('link', { name: 'В работе', exact: true }).click();
  await expect(page).toHaveURL(/\/my-cases$/);
  await expectUnlocked(page);
  await expect(
    page.getByRole('heading', { name: 'В работе', level: 1, exact: true }),
  ).toBeVisible();
  await show.click();
  await expect(dialog).toBeVisible();
  await page.setViewportSize({ width: 1100, height: 900 });
  await expectUnlocked(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await expectUnlocked(page);
  await expect(show).toBeVisible();
  expect(state.unexpected).toEqual([]);
  await expectNoOverflow(page);
});

test('админ использует ту же стрелку, сохраняя поиск списка и незавершённую форму кейса', async ({
  page,
  viewport,
}) => {
  const state = await authLayoutFixture(page);
  await page.goto('/admin');
  const mobile = Boolean(viewport && viewport.width <= 620);
  const search = page.getByRole('searchbox', { name: 'Поиск по названию кейса', exact: true });
  await search.fill('каталогу');
  const caseLink = page.getByRole('link', { name: /Поиск по каталогу.*Черновик/ });
  await expect(caseLink).toBeVisible();
  if (mobile) await page.getByRole('button', { name: showLabel, exact: true }).click();
  await page.getByRole('button', { name: hideLabel, exact: true }).click();
  await expect(search).toHaveValue('каталогу');
  await expect(caseLink).toBeVisible();
  await caseLink.click();
  await expect(page).toHaveURL(new RegExp(`/admin/${caseId}/edit$`));
  const name = page.getByRole('textbox', { name: 'Название кейса', exact: true });
  const description = page.getByRole('textbox', { name: 'Описание', exact: true });
  await name.fill('Новый сценарий переводов');
  const draft = 'Черновик задания, который должен сохраниться при переключении панели.';
  await description.fill(draft);
  await page.getByRole('button', { name: showLabel, exact: true }).click();
  await expect(
    page.getByRole('navigation', { name: 'Основная навигация', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: hideLabel, exact: true }).click();
  await expectUnlocked(page);
  await expect(name).toHaveValue('Новый сценарий переводов');
  await expect(description).toHaveValue(draft);
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: 'Все кейсы', exact: false }).click();
  await expect(page).toHaveURL(new RegExp(`/admin/${caseId}/edit$`));
  await expect(description).toHaveValue(draft);
  expect(state.unexpected).toEqual([]);
  await expect(page.locator('body')).not.toContainText(/лотти|lotty/i);
  await expectNoOverflow(page);
});
