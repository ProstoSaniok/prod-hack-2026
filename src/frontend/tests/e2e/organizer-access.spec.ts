import { expect, test } from '@playwright/test';

test('organizer lands in admin and cannot execute participant cases', async ({ page }) => {
  test.skip(
    !process.env['ORGANIZER_EMAIL'] || !process.env['ORGANIZER_PASSWORD'],
    'Local organizer credentials required',
  );
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(process.env['ORGANIZER_EMAIL'] ?? '');
  await page.getByLabel('Пароль', { exact: true }).fill(process.env['ORGANIZER_PASSWORD'] ?? '');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/, { timeout: 20000 });
  for (const path of ['/catalog', '/my-cases', '/cases/customer-retention/work']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/admin$/);
    await expect(
      page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true }),
    ).toHaveCount(0);
  }
  await page.goto('/profile');
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('link', { name: 'В работе', exact: true })).toHaveCount(0);
});

test('shared admin has no profile and preserves the participant account', async ({ page }) => {
  await page.goto('/admin');
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled({
    timeout: 20000,
  });
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Личный кабинет', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBeNull();
  await page.goto('/login');
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/, { timeout: 20000 });
  if ((page.viewportSize()?.width ?? 1440) <= 620) {
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  }
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(1);
  await page.goto('/admin');
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled();
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(0);
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: 'Профиль и история', exact: true })).toBeVisible();
  if ((page.viewportSize()?.width ?? 1440) <= 620) {
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  }
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(1);
  const email = await page.evaluate(() => {
    const value: unknown = JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}');
    return value && typeof value === 'object' && 'email' in value ? value.email : '';
  });
  expect(email).toBe('performer.local@example.com');
});

test('admin entry from both auth pages opens admin without participant registration', async ({
  page,
}) => {
  for (const path of ['/register', '/login']) {
    await page.goto(path);
    const entry = page.getByRole('link', { name: 'Админ', exact: true });
    await expect(entry).toHaveAttribute('href', '/admin');
    await entry.click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled();
  }
});
