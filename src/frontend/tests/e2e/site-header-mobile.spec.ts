import { expect, test } from '@playwright/test';

for (const path of ['/ui-kit']) {
  test(`мобильное меню доступно с клавиатуры и закрывается после навигации: ${path}`, async ({
    page,
    viewport,
  }) => {
    test.skip(!viewport || viewport.width > 620, 'Мобильная навигация до 620px');
    await page.goto(path);

    const toggle = page.locator('button[aria-controls="main-navigation"]');
    const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
    await expect(navigation).toBeHidden();

    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'О продукте', exact: true })).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(navigation).toBeHidden();
    await expect(toggle).toBeFocused();

    await toggle.click();
    await page.getByRole('link', { name: 'О продукте', exact: true }).click();
    await expect(page).toHaveURL(/#product$/);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(navigation).toBeHidden();

    await page.setViewportSize({ width: 1024, height: 768 });
    await expect(toggle).toBeHidden();
    await expect(navigation).toBeVisible();
  });
}
