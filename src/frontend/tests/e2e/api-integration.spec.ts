import { expect, Page, test } from '@playwright/test';

// Authentication performs password hashing and then three bounded API requests.
test.use({ actionTimeout: 15000 });
test.setTimeout(60000);

test('real registration, persisted session, wallet and logout', async ({ page }) => {
  const email = `integration-${crypto.randomUUID()}@example.com`;
  await page.goto('/register');
  await page.getByLabel('Ваше имя', { exact: true }).fill('Тест API');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill('Integration-test-123');
  await page.getByLabel('Повторите пароль', { exact: true }).fill('Integration-test-123');
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/, { timeout: 30000 });
  await expect(page.getByTestId('credits-balance')).toContainText('10');
  await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
  await page.goto('/profile#wallet');
  await expect(page.getByRole('heading', { name: 'Профиль', exact: true })).toBeVisible();
  await expect(page.getByText('Приветственный бонус', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Приветственный бонус', { exact: true })).toBeVisible();
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('button', { name: 'Выйти из аккаунта' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill('Integration-test-123');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/, { timeout: 30000 });
  await page.goto('/my-cases');
  await expect(page.getByRole('heading', { name: 'В работе', exact: true })).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Проекты', exact: true }).getByRole('link'),
  ).toHaveCount(0);
  // A fresh participant must receive usable server rights, without demo-local projects.
  const capabilities = await page.evaluate(async () => {
    const session = JSON.parse(sessionStorage.getItem('prodguild.api.session.v1') ?? '{}');
    const response = await fetch('/auth/me', {
      headers: { Authorization: `Bearer ${session.token}` },
    });
    if (!response.ok) throw new Error(`auth/me: ${response.status}`);
    return (await response.json()).capabilities;
  });
  expect(capabilities).toContain('participant');
  await page.goto('/profile#wallet');
  await expect(page.getByText('Приветственный бонус', { exact: true })).toHaveCount(1);
});

test('invalid credentials show a server error and do not open a session', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('missing@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('wrong-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Неверный email или пароль', {
    timeout: 15000,
  });
  await expect(page).toHaveURL(/\/login$/);
});

const personalSessionKey = 'prodguild.api.session.v1';
const adminSessionKey = 'prodguild.admin.session.v1';
const legacyData = {
  'lotty.mock.session.v1': '"demo"',
  'lotty.mock.accounts.v1': '{old-accounts',
  'lotty.mock.work.v1.demo': '{old-work',
  'prodguild.solutions.v1.saved': '{"draft":"Не удалять"}',
};

async function seedLegacy(page: Page, malformed = false) {
  const entries = {
    ...legacyData,
    'lotty.mock.session.v1': malformed ? '{invalid-session' : '"demo"',
  };
  await page.addInitScript(values => {
    if (sessionStorage.getItem('prodguild.a1.seeded')) return;
    for (const [key, value] of Object.entries(values)) localStorage.setItem(key, value);
    sessionStorage.setItem('prodguild.a1.seeded', 'true');
  }, entries);
  return async () => {
    expect(
      await page.evaluate(keys => keys.map(key => localStorage.getItem(key)), Object.keys(entries)),
    ).toEqual(Object.values(entries));
  };
}

function watchAuthOnlyMutations(page: Page) {
  const unexpected: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (
      ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method()) &&
      !['/auth/sign_in/email', '/auth/sign_out'].includes(path)
    )
      unexpected.push(`${request.method()} ${path}`);
  });
  return () => expect(unexpected).toEqual([]);
}

async function openAccountMenu(page: Page) {
  if ((page.viewportSize()?.width ?? 1440) <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
}

async function signOutReal(page: Page) {
  await openAccountMenu(page);
  const response = page.waitForResponse(value => value.url().endsWith('/auth/sign_out'));
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  expect((await response).status()).toBe(204);
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(key => sessionStorage.getItem(key), personalSessionKey)).toBeNull();
}

for (const malformed of [false, true]) {
  test(`A1 live: ${malformed ? 'damaged' : 'old demo'} storage cannot sign in`, async ({
    page,
  }, info) => {
    const unchanged = await seedLegacy(page, malformed);
    const authOnly = watchAuthOnlyMutations(page);
    await page.goto('/profile');
    await expect(page).toHaveURL(/\/login\?returnUrl=%2Fprofile$/);
    await expect(page.getByTestId('credits-balance')).toHaveCount(0);
    await page.goto('/my-cases');
    await expect(page).toHaveURL(/\/login\?returnUrl=%2Fmy-cases$/);
    await page.screenshot({ path: info.outputPath('legacy-storage-login.png'), fullPage: true });
    await unchanged();
    authOnly();
  });
}

for (const role of [
  { label: 'Исполнитель', key: 'performer' },
  { label: 'Ревьюер−', key: 'review-minus' },
  { label: 'Ревьюер+', key: 'review-plus' },
  { label: 'Эксперт', key: 'mentor' },
]) {
  test(`A1 live: ${role.label} uses real auth, reload and logout`, async ({ page }, info) => {
    const unchanged = await seedLegacy(page);
    const authOnly = watchAuthOnlyMutations(page);
    await page.goto('/login');
    const login = page.waitForResponse(response => response.url().endsWith('/auth/sign_in/email'));
    const me = page.waitForResponse(response => response.url().endsWith('/auth/me'));
    const wallet = page.waitForResponse(response => response.url().endsWith('/api/v1/me/wallet'));
    await page.getByRole('button', { name: role.label, exact: true }).click();
    const signedIn = await login;
    expect(signedIn.status()).toBe(200);
    expect(signedIn.request().postDataJSON().email).toBe(`${role.key}.local@example.com`);
    const identityResponse = await me;
    expect(identityResponse.status()).toBe(200);
    const identity = (await identityResponse.json()) as { id: string; capabilities: string[] };
    expect(identity.id).toMatch(/^[a-f0-9-]{36}$/);
    expect(identity.capabilities).toContain('participant');
    const walletResponse = await wallet;
    expect(walletResponse.status()).toBe(200);
    const balance = (await walletResponse.json()) as { available: number };
    await expect(page).not.toHaveURL(/\/login(?:\?|$)/);
    // A1 proves authentication. Expert queue access is the separate A2 acceptance step.
    if (role.key === 'mentor')
      info.annotations.push({ type: 'A2 pending', description: `Role landing: ${page.url()}` });
    await page.goto('/catalog');
    await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
    await expect(page.getByTestId('credits-balance')).toHaveAttribute(
      'aria-label',
      `Баланс: ${new Intl.NumberFormat('ru-RU').format(balance.available)} PRODитов. История операций`,
    );
    const restored = page.waitForResponse(response => response.url().endsWith('/auth/me'));
    await page.reload();
    const restoredResponse = await restored;
    expect(restoredResponse.status()).toBe(200);
    expect((await restoredResponse.json()).id).toBe(identity.id);
    await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
    await expect(page.getByTestId('credits-balance')).toHaveAttribute(
      'aria-label',
      `Баланс: ${new Intl.NumberFormat('ru-RU').format(balance.available)} PRODитов. История операций`,
    );
    await openAccountMenu(page);
    await page.screenshot({ path: info.outputPath('real-session-balance.png'), fullPage: true });
    if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
    await unchanged();
    await signOutReal(page);
    await page.goto('/profile');
    await expect(page).toHaveURL(/\/login\?returnUrl=%2Fprofile$/);
    await unchanged();
    authOnly();
  });
}

test('A1 live: admin entry and reload preserve the participant session', async ({ page }, info) => {
  const unchanged = await seedLegacy(page);
  const authOnly = watchAuthOnlyMutations(page);
  await page.goto('/login');
  const me = page.waitForResponse(response => response.url().endsWith('/auth/me'));
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  const participant = (await (await me).json()) as { id: string };
  await expect(page).toHaveURL(/\/catalog$/);
  const personal = await page.evaluate(key => sessionStorage.getItem(key), personalSessionKey);
  const restoredMe = page.waitForResponse(response => response.url().endsWith('/auth/me'));
  await page.goto('/login');
  await (await restoredMe).finished();
  const adminMe = page.waitForResponse(response => response.url().endsWith('/auth/me'));
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  const admin = (await (await adminMe).json()) as { id: string; capabilities: string[] };
  expect(admin.capabilities).toContain('organizer');
  expect(admin.id).not.toBe(participant.id);
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('link', { name: 'Профиль', exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled();
  expect(
    (await page.evaluate(key => sessionStorage.getItem(key), personalSessionKey)) === personal,
  ).toBe(true);
  expect(await page.evaluate(key => !!sessionStorage.getItem(key), adminSessionKey)).toBe(true);
  await page.screenshot({ path: info.outputPath('separate-admin.png'), fullPage: true });
  const profile = page.waitForResponse(response => response.url().endsWith('/api/v1/me/profile'));
  await page.goto('/profile');
  expect((await (await profile).json()).id).toBe(participant.id);
  await expect(page.getByText('performer.local@example.com', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('participant-after-admin.png'), fullPage: true });
  await signOutReal(page);
  expect(await page.evaluate(key => !!sessionStorage.getItem(key), adminSessionKey)).toBe(true);
  await unchanged();
  authOnly();
});

test('A1 live: real sign-in with wallet failure shows unknown balance', async ({ page }, info) => {
  const unchanged = await seedLegacy(page);
  const authOnly = watchAuthOnlyMutations(page);
  // Only the failure is simulated; authentication and all other requests use the real API.
  await page.route('**/api/v1/me/wallet', route => route.fulfill({ status: 503, json: {} }));
  await page.goto('/login');
  const login = page.waitForResponse(response => response.url().endsWith('/auth/sign_in/email'));
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  expect((await login).status()).toBe(200);
  await expect(page).toHaveURL(/\/catalog$/, { timeout: 30000 });
  await expect(page.getByTestId('credits-balance')).toHaveAttribute('aria-label', /Баланс: —/);
  await expect(page.locator('.case-content > [role="status"]')).toContainText(
    'Сервис временно недоступен',
  );
  await openAccountMenu(page);
  await page.screenshot({ path: info.outputPath('wallet-failure.png'), fullPage: true });
  if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
  await signOutReal(page);
  await unchanged();
  authOnly();
});

for (const role of [
  { label: 'Исполнитель', path: '/catalog' },
  { label: 'Ревьюер−', path: '/reviews' },
  { label: 'Ревьюер+', path: '/reviews' },
  { label: 'Эксперт', path: '/expert' },
]) {
  test(`A2 live: ${role.label} navigation and direct routes follow master rights`, async ({
    page,
  }, info) => {
    const authOnly = watchAuthOnlyMutations(page);
    const failedReads: string[] = [];
    page.on('response', response => {
      const url = new URL(response.url());
      if (
        response.request().method() === 'GET' &&
        url.pathname.startsWith('/api/v1/') &&
        response.status() >= 400
      )
        failedReads.push(`${response.status()} ${url.pathname}`);
    });
    await page.goto('/login');
    const modeResponse = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/me/review-mode'),
    );
    const login = page.waitForResponse(response => response.url().endsWith('/auth/sign_in/email'));
    await page.getByRole('button', { name: role.label, exact: true }).click();
    expect((await login).status()).toBe(200);
    const response = await modeResponse;
    expect(response.status()).toBe(200);
    const mode = (await response.json()) as {
      can_review: boolean;
      can_assess_readiness: boolean;
      tasks: { kind: string; status: string }[];
    };
    expect(mode).not.toHaveProperty('can_quality_review');
    expect(typeof mode.can_review).toBe('boolean');
    expect(typeof mode.can_assess_readiness).toBe('boolean');
    expect(Array.isArray(mode.tasks)).toBe(true);
    await info.attach('master-review-access', {
      body: JSON.stringify(mode, null, 2),
      contentType: 'application/json',
    });
    await expect(page).toHaveURL(new RegExp(`${role.path}$`));
    const ordinary =
      mode.can_review ||
      mode.tasks.some(task => task.kind === 'review' && task.status === 'assigned');
    const quality =
      mode.can_assess_readiness ||
      mode.tasks.some(task => task.kind === 'quality_check' && task.status === 'assigned');
    const labels = [
      'Каталог кейсов',
      'В работе',
      ...(ordinary ? ['Ревью'] : []),
      ...(quality ? ['Экспертное ревью'] : []),
      ...(mode.can_assess_readiness ? ['Заявки на эксперта'] : []),
    ];
    await openAccountMenu(page);
    await expect(
      page.getByRole('navigation', { name: 'Основная навигация', exact: true }).getByRole('link'),
    ).toHaveText(labels);
    await page.screenshot({ path: info.outputPath('server-navigation.png') });
    if ((page.viewportSize()?.width ?? 1440) <= 620) await page.keyboard.press('Escape');
    for (const target of [
      { path: '/reviews', allowed: ordinary, title: 'Ревью' },
      { path: '/expert', allowed: quality, title: 'Экспертное ревью' },
      {
        path: '/expert/applications',
        allowed: mode.can_assess_readiness,
        title: 'Заявки на эксперта',
      },
    ]) {
      const dataPath =
        target.path === '/expert/applications'
          ? '/api/v1/reviewer-applications'
          : '/api/v1/me/review-assignments';
      const loaded = target.allowed
        ? page.waitForResponse(
            response =>
              new URL(response.url()).pathname === dataPath &&
              response.request().method() === 'GET',
          )
        : null;
      await page.goto(target.path);
      if (target.allowed) {
        expect((await loaded)?.status()).toBe(200);
        await expect(page).toHaveURL(new RegExp(`${target.path}$`));
        await expect(page).toHaveTitle(target.title);
        await expect(page.locator('app-loading-state')).toHaveCount(0);
        await expect(page.getByRole('alert')).toHaveCount(0);
        const restored = page.waitForResponse(
          response =>
            new URL(response.url()).pathname === dataPath && response.request().method() === 'GET',
        );
        await page.reload();
        expect((await restored).status()).toBe(200);
        await expect(page).toHaveURL(new RegExp(`${target.path}$`));
        await expect(page).toHaveTitle(target.title);
        await expect(page.locator('app-loading-state')).toHaveCount(0);
        await expect(page.getByRole('alert')).toHaveCount(0);
        if (target.path === '/expert')
          await page.screenshot({ path: info.outputPath('expert-view.png') });
      } else {
        await expect(page).toHaveURL(/\/catalog\?reviewAccess=denied$/);
        await expect(
          page
            .getByRole('status')
            .filter({ hasText: 'Ревью доступно участникам с правами ревьюера.' }),
        ).toBeVisible();
        await expect(page.locator('app-loading-state')).toHaveCount(0);
      }
    }
    await page.goto('/catalog');
    await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
    await signOutReal(page);
    expect(failedReads).toEqual([]);
    authOnly();
  });
}
