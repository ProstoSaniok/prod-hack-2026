import { expect, Page, test } from '@playwright/test';

// HTTP fixtures verify frontend integration; they do not replace backend E2E checks.
async function mockBackend(page: Page): Promise<void> {
  let organizer = false;
  await page.route('**/auth/sign_in/email', async route => {
    organizer = route.request().postDataJSON().email === 'organizer.local@example.com';
    await route.fulfill({ json: { access_token: 'local-role-session', token_type: 'bearer' } });
  });
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: organizer ? 'organizer' : 'participant',
        role: 'user',
        capabilities: organizer ? ['organizer'] : [],
      },
    }),
  );
  await page.route('**/api/v1/me/wallet', route =>
    route.fulfill({
      json: {
        settled: 1200,
        reserved: 0,
        available: 1200,
        unit: 'credit',
      },
    }),
  );
  await page.route('**/api/v1/competencies?*', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/cases?*', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/admin/cases?*', route => route.fulfill({ json: [] }));
}

async function navigation(page: Page): Promise<void> {
  const opener = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await opener.isVisible()) await opener.click();
}

test.beforeEach(async ({ page }) => mockBackend(page));

for (const label of ['Исполнитель', 'Ревьюер−', 'Ревьюер+', 'Эксперт']) {
  test(`роль ${label}: вход, единое меню и ревью после обновления`, async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('button', { name: label, exact: true }).click();
    await expect(page).toHaveURL(/\/catalog$/);
    await navigation(page);
    await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(1);
    await expect(page.getByRole('link', { name: 'Завершённые', exact: true })).toHaveCount(0);
    await page.getByRole('link', { name: 'Ревью', exact: true }).click();
    await expect(page).toHaveURL(/\/reviews$/);
    await page.getByRole('link', { name: 'Открыть ревью: Поиск по каталогу', exact: true }).click();
    await expect(page).toHaveURL(/\/reviews\/catalog-search$/);
    await page.reload();
    await expect(
      page.getByRole('textbox', { name: 'Редактор: src/search.ts', exact: true }),
    ).toBeVisible();
  });
}

test('вход организатора сохраняет отдельное меню и блокирует ревью', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('organizer.local@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('Local-demo-2026!');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto('/profile');
  await expect(page).toHaveURL(/\/admin$/);
  await navigation(page);
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Ревью', exact: true })).toHaveCount(0);
  for (const path of ['/reviews', '/reviews/catalog-search', '/catalog', '/my-cases']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/admin$/);
  }
});

test('гость возвращается к ревью после входа через форму', async ({ page }) => {
  await page.goto('/reviews/catalog-search');
  await expect(page).toHaveURL(/\/login\?returnUrl=/);
  await page.getByLabel('Email', { exact: true }).fill('reviewer@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('Integration-test-123');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews\/catalog-search$/);
});

test('редактор и сайдбар сохраняют правки ревью при переключении и обновлении', async ({
  page,
}, testInfo) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto('/reviews/catalog-search');
  const editor = page.getByRole('textbox', { name: 'Редактор: src/search.ts', exact: true });
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('Review integration draft');
  await expect(editor).toHaveText('Review integration draft');
  await navigation(page);
  await page.getByRole('button', { name: 'Скрыть боковую панель', exact: true }).click();
  await expect(editor).toHaveText('Review integration draft');
  await page.reload();
  await expect(editor).toHaveText('Review integration draft');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: `/tmp/prodguild-local-demo-${testInfo.project.name}.png`,
    fullPage: true,
  });
});
