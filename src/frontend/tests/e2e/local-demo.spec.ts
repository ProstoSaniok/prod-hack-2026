import { expect, test } from '@playwright/test';

for (const label of ['Исполнитель', 'Ревьюер−', 'Ревьюер+', 'Эксперт']) {
  test(`local role login: ${label}`, async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Попробовать демо', exact: true })).toHaveCount(
      0,
    );
    const response = page.waitForResponse(
      r => r.url().includes('/auth/sign_in/email') && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: label, exact: true }).click();
    expect((await response).status()).toBe(200);
    await expect(page).toHaveURL(/\/catalog$/);
    await page.reload();
    await expect(page).toHaveURL(/\/catalog$/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}
