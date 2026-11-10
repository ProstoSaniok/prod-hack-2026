import { expect, Page, test } from '@playwright/test';
import {
  authLayoutFixture,
  caseId,
  fixturePassword,
  newFixtureEmail,
  projectId,
  signInFixture,
} from './fixtures/auth-layout';

async function expectAuthShell(page: Page, title: 'Вход' | 'Регистрация'): Promise<void> {
  await expect(page.getByRole('heading', { level: 1, name: title, exact: true })).toBeVisible();
  await expect(page.locator('app-sidebar')).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Показать боковую панель', exact: true }),
  ).toHaveCount(0);
  await expect(page.getByTestId('credits-balance')).toHaveCount(0);
  await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
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
async function expectBalance(page: Page, amount: number): Promise<void> {
  await expect(page.getByTestId('credits-balance')).toHaveAttribute(
    'aria-label',
    `Баланс: ${new Intl.NumberFormat('ru-RU').format(amount)} PRODитов. История операций`,
  );
}
async function logout(page: Page): Promise<void> {
  const mobile = (page.viewportSize()?.width ?? 1440) <= 620;
  if (mobile) {
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toBeVisible();
    await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');
  } else {
    const collapse = page.getByRole('button', { name: 'Скрыть боковую панель', exact: true });
    if (await collapse.isVisible()) await collapse.click();
    await expect(page.locator('app-sidebar')).toHaveCSS('width', '88px');
  }
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expectAuthShell(page, 'Вход');
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toHaveCount(0);
  await expect(page.locator('main')).not.toHaveAttribute('inert', '');
  await page.getByLabel('Email', { exact: true }).click();
  await expect(page.getByLabel('Email', { exact: true })).toBeFocused();
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBeNull();
}
async function register(page: Page, name = 'Анна Практикова'): Promise<void> {
  await page.goto('/register');
  await page.getByLabel('Ваше имя', { exact: true }).fill(name);
  await page.getByLabel('Email', { exact: true }).fill(newFixtureEmail);
  await page.getByLabel('Пароль', { exact: true }).fill(fixturePassword);
  await page.getByLabel('Повторите пароль', { exact: true }).fill(fixturePassword);
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expectBalance(page, 10);
}

test('вход и регистрация имеют отдельную оболочку без навигации кабинета даже после входа', async ({
  page,
}) => {
  const state = await authLayoutFixture(page);
  await page.goto('/login');
  await expectAuthShell(page, 'Вход');
  await page
    .locator('.switch-mode')
    .getByRole('link', { name: 'Зарегистрироваться', exact: true })
    .click();
  await expectAuthShell(page, 'Регистрация');
  await expectNoOverflow(page);
  await page.goto('/login');
  await signInFixture(page);
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.locator('app-sidebar')).toHaveCount(1);
  await page.goto('/login');
  await expectAuthShell(page, 'Вход');
  await page.reload();
  await expectAuthShell(page, 'Вход');
  await page.goto('/register');
  await expectAuthShell(page, 'Регистрация');
  await page.goto('/catalog');
  await expect(page).toHaveURL(/\/catalog$/);
  await expectBalance(page, 37);
  await expectNoOverflow(page);
  expect(state.unexpected).toEqual([]);
});

test('баланс API восстанавливается после обновления, меняется с аккаунтом и исчезает при выходе', async ({
  page,
}) => {
  const state = await authLayoutFixture(page);
  await page.goto('/login');
  await signInFixture(page);
  await expect(page).toHaveURL(/\/catalog$/);
  await expectBalance(page, 37);
  await page.reload();
  await expectBalance(page, 37);
  await logout(page);
  await register(page);
  await page.reload();
  await expectBalance(page, 10);
  await logout(page);
  await signInFixture(page);
  await expect(page).toHaveURL(/\/catalog$/);
  await expectBalance(page, 37);
  expect(state.events.filter(event => event === 'sign-out')).toHaveLength(2);
  expect(state.unexpected).toEqual([]);
  await expectNoOverflow(page);
});

test('вход по returnUrl открывает защищённый проект с решением, сохранённым перед выходом', async ({
  page,
}) => {
  const state = await authLayoutFixture(page);
  await register(page);
  await page.goto(`/projects/${projectId}`);
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true });
  await expect(editor).toContainText('Начальное решение');
  const draft = 'Проверяю поиск, категории и обработку пустого результата.';
  await editor.fill(draft);
  await logout(page);
  expect(state.file.content).toBe(draft);
  expect(state.events.indexOf('file-saved')).toBeLessThan(state.events.indexOf('sign-out'));
  await page.goto(`/projects/${projectId}`);
  await expectAuthShell(page, 'Вход');
  await expect(page).toHaveURL(new RegExp(`/login\\?returnUrl=%2Fprojects%2F${projectId}$`));
  await signInFixture(page, newFixtureEmail);
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));
  await expect(editor).toHaveText(draft);
  await expectBalance(page, 10);
  await expect(
    page.getByRole('list', { name: 'Этапы работы' }).locator('[aria-current="step"]'),
  ).toContainText('В работе');
  expect(state.unexpected).toEqual([]);
  await expectNoOverflow(page);
});

test('длинное имя и большой баланс API помещаются в профиле и боковой панели', async ({ page }) => {
  const state = await authLayoutFixture(page);
  const name = 'Ш'.repeat(60);
  await register(page, name);
  const account = state.accounts.get(newFixtureEmail);
  if (!account) throw new Error('Fixture account must exist after registration');
  account.balance = 999999999;
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  await expect(page.locator('.wallet-panel .currency-number')).toHaveText('999 999 999');
  const mobile = (page.viewportSize()?.width ?? 1440) <= 620;
  if (mobile)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  const balance = page.getByTestId('credits-balance');
  await expectBalance(page, 999999999);
  await expect(balance).toBeVisible();
  await expect
    .poll(() =>
      balance.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return (
          rect.left >= 0 &&
          rect.right <= document.documentElement.clientWidth &&
          rect.top >= 0 &&
          rect.bottom <= window.innerHeight
        );
      }),
    )
    .toBe(true);
  if (mobile) await page.keyboard.press('Escape');
  expect(state.unexpected).toEqual([]);
  await expectNoOverflow(page);
});

const legacySessionKey = 'lotty.mock.session.v1';
const legacyEntries = {
  [legacySessionKey]: JSON.stringify('demo'),
  'lotty.mock.accounts.v1': '[{"login":"saved-account","name":"Старые данные"}]',
  'lotty.mock.work.v1.demo': '{damaged-work',
  'prodguild.solutions.v1.saved': '{"draft":"Сохранённый текст"}',
};

async function preserveLegacyStorage(page: Page, session = JSON.stringify('demo')) {
  const entries = { ...legacyEntries, [legacySessionKey]: session };
  await page.addInitScript(values => {
    if (sessionStorage.getItem('prodguild.a1.storage-seeded')) return;
    for (const [key, value] of Object.entries(values)) localStorage.setItem(key, value);
    sessionStorage.setItem('prodguild.a1.storage-seeded', 'true');
  }, entries);
  return async () => {
    expect(
      await page.evaluate(keys => keys.map(key => localStorage.getItem(key)), Object.keys(entries)),
    ).toEqual(Object.values(entries));
  };
}

for (const stored of ['"demo"', '{broken-session', '{"login":"demo"}']) {
  test(`A1: legacy storage ${stored} does not authorize or change`, async ({ page }) => {
    test.setTimeout(60_000);
    const unchanged = await preserveLegacyStorage(page, stored);
    await authLayoutFixture(page);
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    for (const path of [
      '/profile',
      '/my-cases',
      '/my-cases/completed',
      '/reviews',
      '/reviews/missing',
      `/cases/${caseId}/work`,
    ]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/login(?:\?|$)/);
      await expect(page.getByTestId('credits-balance')).toHaveCount(0);
      await unchanged();
    }
    await page.goto('/catalog');
    await expect(page.locator('app-case-shell')).toBeVisible();
    await expect(page.getByTestId('credits-balance')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Выйти из аккаунта' })).toHaveCount(0);
    await unchanged();
    expect(pageErrors).toEqual([]);
  });
}

test('A1: expired API session cannot fall back to legacy demo', async ({ page }) => {
  const unchanged = await preserveLegacyStorage(page);
  await authLayoutFixture(page);
  await page.addInitScript(() => {
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'expired', email: 'expired@example.com', name: 'Expired' }),
    );
  });
  const rejected = page.waitForResponse(response => response.url().endsWith('/auth/me'));
  await page.goto('/profile');
  expect((await rejected).status()).toBe(401);
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByTestId('credits-balance')).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBeNull();
  await unchanged();
});

for (const failure of ['unavailable', 'malformed']) {
  test(`A1: ${failure} wallet does not invent a balance`, async ({ page }) => {
    const unchanged = await preserveLegacyStorage(page);
    await authLayoutFixture(page);
    await page.route('**/api/v1/me/wallet', route =>
      failure === 'unavailable'
        ? route.fulfill({ status: 503, json: {} })
        : route.fulfill({ status: 200, contentType: 'application/json', body: '{invalid' }),
    );
    await page.goto('/login');
    await signInFixture(page);
    await expect(page).toHaveURL(/\/catalog$/);
    await expect(page.getByTestId('credits-balance')).toHaveAttribute('aria-label', /Баланс: —/);
    await expect(page.locator('.case-content > [role="status"]')).toContainText(
      failure === 'unavailable' ? 'Сервис временно недоступен' : 'Не удалось связаться с сервером',
    );
    await page.reload();
    await expect(page.getByTestId('credits-balance')).toHaveAttribute('aria-label', /Баланс: —/);
    await logout(page);
    await unchanged();
  });
}

test('A1: failed logout retains the real session and legacy storage', async ({ page }) => {
  const unchanged = await preserveLegacyStorage(page);
  const state = await authLayoutFixture(page);
  await page.goto('/login');
  await signInFixture(page);
  await expectBalance(page, 37);
  const session = await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'));
  await page.route('**/auth/sign_out', route => route.fulfill({ status: 503, json: {} }));
  if ((page.viewportSize()?.width ?? 1440) <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page.locator('.case-content > [role="alert"]')).toContainText(
    'Сервис временно недоступен',
  );
  await expect(page).toHaveURL(/\/catalog$/);
  await expectBalance(page, 37);
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBe(
    session,
  );
  await unchanged();
  await page.unroute('**/auth/sign_out');
  await logout(page);
  expect(state.events.filter(event => event === 'sign-out')).toHaveLength(1);
  await unchanged();
});
