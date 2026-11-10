import { expect, test } from '@playwright/test';

const record = {
  id: 'support',
  title: 'Умный помощник поддержки',
  description: 'Создайте помощника, который находит информацию в базе знаний.',
  expected_result: 'Работающий поиск по базе знаний.',
  created_at: '2026-10-17T10:00:00Z',
  version: 1,
  status: 'draft',
  difficulty: 'review_plus',
  frozen: false,
  competencies: [{ competency_id: 'sql', required_value: 50, weight: 0.1 }],
};

test.beforeEach(async ({ page }) => {
  await page.route('**/auth/sign_in/email', route =>
    route.fulfill({ json: { access_token: 'admin-test', token_type: 'bearer' } }),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'admin-test', role: 'user', capabilities: ['organizer'] } }),
  );
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/competencies')
      return route.fulfill({ json: [{ id: 'sql', name: 'SQL', description: 'Запросы' }] });
    if (url.pathname === '/api/v1/admin/cases')
      return route.fulfill({
        json: [record, { ...record, id: 'finance', title: 'Аналитика личных финансов' }],
      });
    if (url.pathname === '/api/v1/admin/cases/support') return route.fulfill({ json: record });
    if (url.pathname.endsWith('/reference-solution'))
      return route.fulfill({ status: 404, json: {} });
    if (url.pathname.endsWith('/files')) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: {} });
  });
});

test('organizer entry opens the case library and survives reload', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/register');
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('heading', { name: 'Кейсы', level: 1, exact: true })).toBeVisible();
  await expect(page.locator('.case-row')).toHaveCount(2);
  await expect(page.getByRole('navigation', { name: 'Страницы кейсов' })).toHaveCount(0);
  await page.reload();
  await expect(page.locator('.case-row')).toHaveCount(2);
  await expect(page.getByRole('link', { name: 'Профиль и история', exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('admin-list.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('name search is case insensitive, explains empty results and resets immediately', async ({
  page,
}) => {
  await page.goto('/admin');
  const search = page.getByRole('searchbox', { name: 'Поиск по названию кейса' });
  const list = page.getByRole('region', { name: 'Созданные кейсы' });
  await search.click();
  await search.pressSequentially('  ФИНАНСОВ  ');
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await expect(list.getByText('Аналитика личных финансов', { exact: true })).toBeVisible();
  await expect(page.getByText('Найдено: 1 из 2')).toBeVisible();
  await search.fill('несуществующий');
  await expect(page.getByRole('heading', { name: 'Кейсы не найдены' })).toBeVisible();
  await page.getByRole('button', { name: 'Сбросить поиск' }).click();
  await expect(search).toHaveValue('');
  await expect(list.getByRole('listitem')).toHaveCount(2);
});

test('keyboard navigation opens a case editor with the server description', async ({ page }) => {
  await page.goto('/admin');
  const link = page.getByRole('link', { name: /Умный помощник поддержки/ });
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/admin\/support\/edit$/);
  await expect(page.getByRole('heading', { name: record.title, level: 1 })).toBeVisible();
  await expect(page.getByLabel('Описание', { exact: true })).toHaveValue(record.description);
  await expect(page.getByRole('link', { name: 'Материалы', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Эталонное решение', exact: true })).toBeVisible();
});

test('leaving an unresolved creation needs confirmation and retains its intent', async ({
  page,
}) => {
  let creates = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/admin/cases')
      creates++;
  });
  await page.route('**/api/v1/admin/cases', route =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 503, json: { code: 'DEPENDENCY_UNAVAILABLE' } })
      : route.fallback(),
  );
  await page.goto('/admin');
  await page.getByRole('link', { name: 'Создать кейс', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/new$/);
  await page.getByLabel('Название кейса', { exact: true }).fill('Несохранённый кейс');
  await expect(page.getByRole('alert')).toBeVisible();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: 'Все кейсы', exact: true }).click();
  await expect(page).toHaveURL(/\/admin\/new\?draft=/);
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue(
    'Несохранённый кейс',
  );
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('link', { name: 'Все кейсы', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  expect(creates).toBe(1);
});

test('long case metadata stays within the page at every viewport', async ({ page }, info) => {
  await page.route('**/api/v1/admin/cases/support', route =>
    route.fulfill({ json: { ...record, title: 'Я'.repeat(500), description: 'Д'.repeat(3000) } }),
  );
  await page.goto('/admin/support/edit');
  await expect(page.getByLabel('Название кейса', { exact: true })).toHaveValue('Я'.repeat(500));
  await page.evaluate(() => document.fonts.ready);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath('admin-long-metadata.png'), fullPage: true });
});
