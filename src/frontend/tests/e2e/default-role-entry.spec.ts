import { expect, test } from '@playwright/test';

// Uses the seeded local backend: no request interception or browser-only demo users.
for (const role of [
  { label: 'Исполнитель', email: 'performer.local@example.com', path: '/catalog' },
  { label: 'Ревьюер−', email: 'review-minus.local@example.com', path: '/reviews' },
  { label: 'Ревьюер+', email: 'review-plus.local@example.com', path: '/reviews' },
  { label: 'Эксперт', email: 'mentor.local@example.com', path: '/expert' },
]) {
  test(`${role.label}: real sign-in, server page and restored identity`, async ({ page }, info) => {
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Ревьюер−', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Ревьюер+', exact: true })).toBeVisible();
    const loginResponse = page.waitForResponse(response =>
      response.url().endsWith('/auth/sign_in/email'),
    );
    const dataPath = role.path === '/catalog' ? '/api/v1/cases?' : '/api/v1/me/review-assignments?';
    const dataResponse = page.waitForResponse(response => response.url().includes(dataPath));
    const reviewModeResponse = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/me/review-mode'),
    );
    await page.getByRole('button', { name: role.label, exact: true }).click();
    const signedIn = await loginResponse;
    expect(signedIn.status()).toBe(200);
    expect(signedIn.request().postDataJSON().email).toBe(role.email);
    await expect(page).toHaveURL(new RegExp(`${role.path}$`));
    expect((await dataResponse).status()).toBe(200);
    const reviewMode = (await (await reviewModeResponse).json()) as { can_review: boolean };
    await expect(page.locator('.topbar, .header-name')).toHaveCount(0);
    const restoredData = page.waitForResponse(response => response.url().includes(dataPath));
    await page.reload();
    expect((await restoredData).status()).toBe(200);
    await expect(page.locator('.topbar, .header-name')).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
    if ((page.viewportSize()?.width ?? 1440) <= 620) {
      await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    }
    const expectedLabels =
      role.label === 'Эксперт'
        ? ['Каталог кейсов', 'В работе', 'Ревью', 'Экспертное ревью', 'Заявки на эксперта']
        : ['Каталог кейсов', 'В работе', ...(reviewMode.can_review ? ['Ревью'] : [])];
    await expect(
      page.getByRole('navigation', { name: 'Основная навигация', exact: true }).getByRole('link'),
    ).toHaveText(expectedLabels);
    await expect(page.getByRole('link', { name: 'Отправленные решения', exact: true })).toHaveCount(
      0,
    );
    if (role.label === 'Эксперт') {
      await expect(
        page.getByRole('link', { name: 'Экспертное ревью', exact: true }),
      ).toHaveAttribute('href', '/expert');
    }
    if ((page.viewportSize()?.width ?? 1440) <= 620) {
      await page.getByRole('button', { name: 'Скрыть боковую панель', exact: true }).click();
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBeLessThanOrEqual(1);
    await page.screenshot({ path: info.outputPath('role-landing.png'), fullPage: true });
    const profileResponse = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/me/profile'),
    );
    const policyResponse = page.waitForResponse(response =>
      response.url().endsWith('/api/v1/development/policy'),
    );
    await page.goto('/profile');
    await expect(page.getByText(role.email, { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Баланс', exact: true })).toBeVisible();
    const profileResult = await profileResponse;
    const policyResult = await policyResponse;
    expect(profileResult.status()).toBe(200);
    expect(policyResult.status()).toBe(200);
    const profile = (await profileResult.json()) as {
      competencies: {
        name: string;
        grade: string;
        value: number;
        next_grade: { xp_remaining: number } | null;
      }[];
      wallet: { available: number };
    };
    const policy = (await policyResult.json()) as {
      grade_gates: { grade: string; minimum_value: number }[];
    };
    const grades = ['performer', 'review_minus', 'review_plus', 'expert'];
    const format = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    for (const skill of profile.competencies) {
      const grade = grades.indexOf(skill.grade) + 1;
      const current =
        policy.grade_gates.find(gate => gate.grade === skill.grade)?.minimum_value ?? 0;
      const next =
        grade < 3 ? policy.grade_gates.find(gate => gate.grade === grades[grade]) : undefined;
      const maximum = next ? next.minimum_value - current : 1;
      const achieved = policy.grade_gates.find(gate => gate.grade === skill.grade)?.minimum_value;
      expect(maximum).toBeGreaterThan(0);
      expect(achieved).toBeGreaterThanOrEqual(0);
      const row = page.locator('.skill-card').filter({
        has: page.getByRole('heading', { name: skill.name, exact: true }),
      });
      await expect(row.locator('.skill-grade')).toContainText(`Грэйд ${grade}`);
      await expect(row.locator('.skill-xp')).toHaveText(`${format.format(skill.value)} XP`);
      const progress = row.getByRole('progressbar');
      await expect(progress).toHaveAttribute('aria-valuemax', String(maximum));
      await expect(progress).toHaveAttribute(
        'aria-valuenow',
        String(
          skill.grade === 'expert' || skill.grade === 'review_plus'
            ? maximum
            : Math.max(0, Math.min(maximum, maximum - (skill.next_grade?.xp_remaining ?? 0))),
        ),
      );
    }
    await expect(page.locator('.wallet-panel .balance .currency-number')).toHaveText(
      format.format(profile.wallet.available),
    );
    await expect(page.locator('.wallet-panel .balance img')).toHaveAttribute(
      'src',
      '/imgs/logos/logo-currency.svg',
    );
    const account = role.email.split('@')[0];
    await page.screenshot({
      path: `/tmp/prodguild-real-profile-${account}-${info.project.name}.png`,
      fullPage: true,
    });
    const sql = page.locator('.skill-card').filter({
      has: page.getByRole('heading', { name: 'SQL', exact: true }),
    });
    if (await sql.count())
      await sql.screenshot({
        path: `/tmp/prodguild-real-sql-${account}-${info.project.name}.png`,
      });
  });
}

test('Админ opens separate panel without personal identity and preserves participant', async ({
  page,
}) => {
  await page.goto('/login');
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled();
  await expect(page.getByRole('link', { name: 'Профиль', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem('prodguild.api.session.v1'))).toBeNull();
  await page.reload();
  await expect(page.getByRole('link', { name: 'Создать кейс', exact: true })).toBeEnabled();
  await page.goto('/login');
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto('/login');
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByRole('link', { name: 'Профиль', exact: true })).toHaveCount(0);
  await page.goto('/profile');
  await expect(page.getByText('performer.local@example.com', { exact: true })).toBeVisible();
});
