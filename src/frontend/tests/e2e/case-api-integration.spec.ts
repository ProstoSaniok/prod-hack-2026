import { expect, test } from '@playwright/test';

test('organizer creates, edits, archives, restores and deletes a real case', async ({ page }) => {
  test.skip(
    !process.env['ORGANIZER_EMAIL'] || !process.env['ORGANIZER_PASSWORD'],
    'Set credentials for a local organizer',
  );
  test.setTimeout(90000);
  await page.goto('/register');
  const loginResponse = page.waitForResponse(
    response =>
      response.url().endsWith('/auth/sign_in/email') && response.request().method() === 'POST',
  );
  const casesResponse = page.waitForResponse(
    response =>
      response.url().includes('/api/v1/admin/cases?') && response.request().method() === 'GET',
  );
  await expect(page.getByRole('button', { name: 'Админ (организатор)', exact: true })).toHaveCount(
    0,
  );
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  expect((await loginResponse).status()).toBe(200);
  expect((await casesResponse).status()).toBe(200);
  await expect(page).toHaveURL(/\/admin$/, { timeout: 30000 });
  await page.getByRole('link', { name: 'Создать кейс', exact: true }).click();
  const title = `SQL ${crypto.randomUUID()}`;
  await page.getByLabel('Название кейса', { exact: true }).fill(title);
  await page
    .getByLabel('Описание', { exact: true })
    .fill('Исправьте SQL-запрос и объясните результат.');
  await page
    .getByLabel('Ожидаемый результат', { exact: true })
    .fill('Корректный SQL и объяснение.');
  await page.getByRole('button', { name: 'Компетенции кейса', exact: true }).click();
  await page.getByRole('checkbox', { name: 'SQL', exact: true }).check();
  await page.getByRole('button', { name: 'Готово', exact: true }).click();
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено', { timeout: 15000 });
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible({
    timeout: 15000,
  });
  await expect(page).toHaveURL(/\/admin\/[^/]+\/edit$/);
  await page.getByRole('link', { name: 'Материалы', exact: true }).click();
  await page.reload();
  await page.getByRole('link', { name: 'Описание', exact: true }).click();
  await page.getByLabel('Название кейса', { exact: true }).fill(title + ' обновлён');
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено', { timeout: 15000 });
  await expect(page.getByRole('heading', { name: title + ' обновлён', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Архивировать', exact: true }).click();
  await expect(page.getByText('Архив', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Восстановить черновик', exact: true }).click();
  await expect(page.getByText('Черновик', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Удалить кейс', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить удаление', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText(title + ' обновлён', { exact: true })).toHaveCount(0);
});

test('participant can open shared admin without a personal organizer profile', async ({ page }) => {
  await page.goto('/register?returnUrl=%2Fadmin');
  await page.getByLabel('Ваше имя', { exact: true }).fill('Участник');
  await page
    .getByLabel('Email', { exact: true })
    .fill(`participant-${crypto.randomUUID()}@example.com`);
  await page.getByLabel('Пароль', { exact: true }).fill('Integration-test-123');
  await page.getByLabel('Повторите пароль', { exact: true }).fill('Integration-test-123');
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/, { timeout: 30000 });
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled({
    timeout: 15000,
  });
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Личный кабинет', exact: true })).toHaveCount(0);
});
