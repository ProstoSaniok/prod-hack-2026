import { expect, Locator, Page, test } from '@playwright/test';
import { formatProditsAmount } from '../../src/app/shared/prodits-presentation';

// These HTTP fixtures exercise the UI contract; backend promotion remains authoritative.
function policyData() {
  return {
    version: 'prodguild-development-v1',
    progression_version: 'p02-v1',
    state: 'configured',
    formula: 'score * weight * (1 + 0.2 * E / 100)',
    success_score: 70,
    quality_score: 70,
    probation_required_reviews: 3,
    display_cap: 100,
    grade_gates: ['performer', 'review_minus', 'review_plus', 'expert'].map((grade, index) => ({
      grade,
      minimum_value: [0, 25, 50, 75][index],
      successful_cases: index ? 2 : 0,
      confirmed_good_reviews: index > 1 ? 3 : 0,
      previous_probation_required: index > 1,
      independent_readiness_required: index === 3,
    })),
  };
}
function profileData() {
  return {
    id: 'profile-user',
    display_name: 'Александра',
    grade: 'expert',
    competencies: [
      { name: 'Python', grade: 'performer', value: 0 },
      { name: 'SQL', grade: 'review_minus', value: 100 },
      { name: 'Проектирование интерфейсов', grade: 'review_plus', value: 58.25 },
      { name: 'Архитектура распределённых систем', grade: 'expert', value: 100 },
      { name: 'Аналитика продукта', grade: 'review_minus', value: 75 },
    ].map((skill, index) => ({
      ...skill,
      competency_id: `skill-${index}`,
      contribution_sum: skill.value,
      version: 2,
      grant: index ? 'confirmed' : 'none',
      review_allowed: index > 0,
      expert_confirmed: skill.grade === 'expert',
      next_grade:
        index === 3
          ? null
          : {
              grade: ['review_minus', 'review_plus', 'expert', '', 'review_plus'][index],
              xp_remaining: [25, 0, 16.75, 0, 0][index],
              successful_cases: index ? 2 : 0,
              previous_probation_passed: index === 0 || index === 2,
            },
      probation:
        index === 1
          ? {
              id: 'probation-sql',
              competency_id: 'skill-1',
              grade: skill.grade,
              confirmed_reviews: 1,
              remaining: 2,
              required_successes: 3,
              passed: false,
            }
          : null,
    })),
    wallet: { settled: 10, reserved: 0, available: 10, unit: 'PRODиты' },
    karma: 4,
    prodits: 10,
  };
}
function transactions(count = 1, offset = 0) {
  return Array.from({ length: count }, (_, index) => ({
    id: `transaction-${index + offset}`,
    sequence: index + offset + 1,
    kind: offset ? 'review_reward' : 'welcome',
    settled_delta: offset ? 5 : 10,
    reserved_delta: 0,
    created_at: '2026-10-17T10:00:00Z',
  }));
}
interface ProfileFixture {
  profile: unknown;
  policy: unknown;
  profileStatus: number;
  policyStatus: number;
  historyStatus: number;
  history: ReturnType<typeof transactions>;
  profileGate?: Promise<void>;
  requests: { path: string; offset: string | null; authorization: string }[];
}
async function fixture(page: Page): Promise<ProfileFixture> {
  const state: ProfileFixture = {
    profile: profileData(),
    policy: policyData(),
    profileStatus: 200,
    policyStatus: 200,
    historyStatus: 200,
    history: transactions(),
    requests: [],
  };
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({
        token: 'profile-test',
        name: 'Александра Константинопольская-Рождественская',
        email: 'alexandra.konstantinopolskaya.rozhdestvenskaya@example.com',
      }),
    ),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({
      json: {
        id: 'profile-user',
        role: 'user',
        capabilities: ['participant'],
      },
    }),
  );
  await page.route('**/auth/sign_out', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    state.requests.push({
      path: url.pathname,
      offset: url.searchParams.get('offset'),
      authorization: route.request().headers()['authorization'],
    });
    if (url.pathname === '/api/v1/me/profile') {
      await state.profileGate;
      return route.fulfill({ status: state.profileStatus, json: state.profile });
    }
    if (url.pathname === '/api/v1/development/policy')
      return route.fulfill({ status: state.policyStatus, json: state.policy });
    if (url.pathname === '/api/v1/me/review-mode')
      return route.fulfill({ json: { enabled: false, status: 'disabled', can_review: true } });
    if (url.pathname === '/api/v1/me/wallet') return route.fulfill({ json: profileData().wallet });
    if (url.pathname === '/api/v1/me/wallet/transactions')
      return route.fulfill({ status: state.historyStatus, json: state.history });
    return route.fulfill({ json: [] });
  });
  return state;
}
async function refreshProfile(page: Page): Promise<void> {
  await page.waitForTimeout(1100);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
}
const skillRegion = (page: Page) => page.getByRole('region', { name: 'Грэйд', exact: true });
const historyRegion = (page: Page) =>
  page.getByRole('region', { name: 'История операций', exact: true });
const sparkleMotion = (point: Locator) =>
  point.evaluate(element => {
    const style = getComputedStyle(element, '::before');
    return {
      transform: style.transform,
      animation: style.animationName,
      iterations: style.animationIterationCount,
    };
  });

test('each skill shows XP to its next grade without granting rights from XP', async ({
  page,
  viewport,
}, info) => {
  const state = await fixture(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/profile');
  const cards = page.locator('.skill-card');
  await expect(cards).toHaveCount(5);
  for (const [index, label] of [
    'Грэйд 1 0 XP',
    'Грэйд 2 100 XP',
    'Грэйд 3 58,25 XP',
    'Грэйд 4 100 XP',
    'Грэйд 2 75 XP',
  ].entries()) {
    await expect(cards.nth(index).locator('.skill-grade')).toContainText(
      label.split(' ').slice(0, 2).join(' '),
    );
    await expect(cards.nth(index).locator('.skill-xp')).toHaveText(
      label.split(' ').slice(2).join(' '),
    );
  }
  await expect(cards.nth(0).getByRole('progressbar')).toHaveAttribute(
    'aria-valuetext',
    'До уровня «Ревьюер−»: 25 XP.',
  );
  await expect(cards.nth(1)).not.toContainText('Порог XP для уровня');
  await expect(cards.nth(1)).not.toContainText('Завершите испытательный период');
  await expect(
    skillRegion(page).getByRole('list', { name: 'Условия следующего уровня' }),
  ).toHaveCount(0);
  await expect(
    skillRegion(page).getByRole('heading', { name: 'Грэйд', exact: true }),
  ).toBeVisible();
  await expect(skillRegion(page).locator('details, summary')).toHaveCount(0);
  await expect(skillRegion(page)).not.toContainText('Условия повышения');
  for (const [index, value] of [0, 25, 1, 1, 25].entries()) {
    const progress = cards.nth(index).getByRole('progressbar');
    await expect(progress).toHaveAttribute('aria-valuemin', '0');
    await expect(progress).toHaveAttribute(
      'aria-valuemax',
      index === 2 || index === 3 ? '1' : '25',
    );
    await expect(progress).toHaveAttribute('aria-valuenow', String(value));
    await expect(progress).toHaveAccessibleName(
      index === 2 || index === 3 ? /максимальный уровень$/ : /до следующего уровня$/,
    );
  }
  const expertPath = cards
    .nth(2)
    .getByRole('region', { name: 'Проектирование интерфейсов: экспертный уровень' });
  await expect(expertPath.getByRole('heading', { name: 'Заявка на эксперта' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Как стать экспертом' })).toContainText(
    'Заявка подаётся отдельно для каждого навыка',
  );
  await expect(
    cards.nth(0).getByRole('button', { name: 'Подать заявку на эксперта', exact: true }),
  ).toBeDisabled();
  await expect(cards.nth(0)).toContainText('Сначала достигните уровня «Ревьюер+»');
  await expect(
    expertPath.getByRole('button', { name: 'Подать заявку на эксперта', exact: true }),
  ).toBeDisabled();
  await expect(expertPath).toContainText('До порога для заявки — 16,75 XP');
  await expect(expertPath).toContainText('Успешные кейсы: 2 из 2');
  await expect(expertPath.getByRole('progressbar')).toHaveCount(0);
  await expect(cards.nth(2).locator('.scale-labels')).not.toContainText('Эксперт');
  await expect(cards.nth(2).getByRole('progressbar')).toHaveAttribute(
    'aria-valuetext',
    'Ревьюер+ — максимальный уровень ревьюера.',
  );
  await expect(cards.nth(3).locator('.expert-progress')).toHaveCount(1);
  await expect(cards.nth(3).getByRole('progressbar')).toHaveAttribute(
    'aria-valuetext',
    'Эксперт — максимальный уровень.',
  );
  await expect(cards.nth(4).locator('.expert-dots')).toHaveCount(0);
  const fillRatios = await cards.evaluateAll(items =>
    items
      .filter(item => item.querySelector('.skill-track'))
      .map(item => {
        const track = item.querySelector('.skill-track') as HTMLElement;
        const fill = item.querySelector('.skill-fill') as HTMLElement;
        return fill.getBoundingClientRect().width / track.getBoundingClientRect().width;
      }),
  );
  [0, 1, 1, 1].forEach((ratio, index) => expect(fillRatios[index]).toBeCloseTo(ratio, 2));
  const [first, second] = await Promise.all([
    cards.nth(0).boundingBox(),
    cards.nth(1).boundingBox(),
  ]);
  expect(
    first &&
      second &&
      (viewport && viewport.width >= 1000 ? first.y === second.y : first.y < second.y),
  ).toBe(true);
  expect(state.requests.filter(request => request.path.endsWith('/me/skills'))).toHaveLength(0);
  expect(
    state.requests
      .filter(request => request.path.endsWith('/profile') || request.path.endsWith('/policy'))
      .every(request => request.authorization === 'Bearer profile-test'),
  ).toBe(true);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  expect(
    await cards.evaluateAll(
      items => items.filter(item => item.scrollWidth > item.clientWidth + 1).length,
    ),
  ).toBe(0);
  await page.screenshot({
    path: `/tmp/prodguild-profile-grades-${info.project.name}.png`,
    fullPage: true,
  });
  await cards.nth(1).screenshot({
    path: `/tmp/prodguild-sql-grade2-100xp-${info.project.name}.png`,
  });
  expect(errors).toEqual([]);
});

test('profile loading, empty and error retries leave history available', async ({ page }) => {
  const state = await fixture(page);
  let release = () => {};
  state.profileGate = new Promise<void>(resolve => (release = resolve));
  state.profile = { ...profileData(), competencies: [] };
  await page.goto('/profile');
  await expect(page.getByRole('progressbar', { name: 'Загружаем профиль…' })).toBeVisible();
  await expect(
    skillRegion(page).getByText('Решайте кейсы — здесь появятся ваши навыки и XP.'),
  ).toHaveCount(0);
  await expect(historyRegion(page)).toContainText('Приветственный бонус');
  release();
  await expect(skillRegion(page).getByRole('link', { name: 'Выбрать кейс' })).toBeVisible();
  state.profileStatus = 503;
  await refreshProfile(page);
  await expect(page.locator('.profile-page > [role="alert"]')).toBeVisible();
  await expect(skillRegion(page).getByRole('link', { name: 'Выбрать кейс' })).toHaveCount(0);
  state.profileStatus = 200;
  state.profile = profileData();
  await page.getByRole('button', { name: 'Повторить загрузку профиля' }).click();
  await expect(page.locator('.skill-card')).toHaveCount(5);
  await expect(page.locator('.profile-page > [role="alert"]')).toHaveCount(0);
});

test('a rounded XP boundary keeps the exact remaining requirement and stored grade', async ({
  page,
}) => {
  const state = await fixture(page);
  const profile = profileData();
  const skill = profile.competencies[0];
  skill.value = 25;
  skill.contribution_sum = 25;
  if (skill.next_grade) skill.next_grade.xp_remaining = 1e-30;
  state.profile = { ...profile, competencies: [skill] };
  await page.goto('/profile');
  const card = page.locator('.skill-card');
  await expect(card.locator('.skill-grade')).toHaveText('Грэйд 1 · Исполнитель');
  await expect(card.getByRole('progressbar')).toHaveAttribute(
    'aria-valuetext',
    'До уровня «Ревьюер−»: меньше 0,01 XP.',
  );
  await expect(card).not.toContainText('Порог XP');
});

test('policy failure keeps Grades and XP and retry resolves grade gates by name', async ({
  page,
}) => {
  const state = await fixture(page);
  state.policyStatus = 503;
  const profile = profileData();
  profile.competencies[1].value = 32;
  profile.competencies[1].contribution_sum = 32;
  const next = profile.competencies[1].next_grade;
  if (next) next.xp_remaining = 18;
  state.profile = profile;
  await page.goto('/profile');
  const sql = page
    .locator('.skill-card')
    .filter({ has: page.getByRole('heading', { name: 'SQL', exact: true }) });
  await expect(sql.locator('.skill-grade')).toHaveText('Грэйд 2 · Ревьюер−');
  await expect(sql.locator('.skill-xp')).toHaveText('32 XP');
  await expect(sql.getByRole('progressbar')).toHaveCount(0);
  await expect(skillRegion(page).getByRole('alert')).toBeVisible();
  await expect(page.locator('.balance')).toHaveText('10 PRODитов');
  state.policyStatus = 200;
  const policy = policyData();
  policy.grade_gates[3].minimum_value = 80;
  policy.grade_gates.reverse();
  state.policy = policy;
  await page.getByRole('button', { name: 'Повторить загрузку шкал' }).click();
  await expect(sql.locator('.skill-grade')).toHaveText('Грэйд 2 · Ревьюер−');
  await expect(sql.locator('.skill-xp')).toHaveText('32 XP');
  await expect(sql.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '25');
  await expect(sql.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '7');
  await expect(skillRegion(page).getByRole('alert')).toHaveCount(0);
});

test('invalid server grade fails closed and refresh preserves previously loaded profile', async ({
  page,
}) => {
  const state = await fixture(page);
  const invalid = profileData();
  invalid.competencies[1].grade = 'unknown';
  state.profile = invalid;
  await page.goto('/profile');
  await expect(page.locator('.profile-page > [role="alert"]')).toBeVisible();
  await expect(page.getByRole('list', { name: 'Ваши навыки' })).toHaveCount(0);
  state.profile = profileData();
  await page.getByRole('button', { name: 'Повторить загрузку профиля' }).click();
  await expect(page.locator('.skill-card')).toHaveCount(5);
  state.profileStatus = 503;
  await refreshProfile(page);
  await expect(page.locator('.profile-page > [role="alert"]')).toBeVisible();
  await expect(page.locator('.skill-card')).toHaveCount(5);
});

test('invalid expert maximum is retryable and only stored expert receives the terminal effect', async ({
  page,
}) => {
  const state = await fixture(page);
  const policy = policyData();
  policy.grade_gates[3].minimum_value = 0;
  state.policy = policy;
  const profile = profileData();
  profile.competencies[3].value = 5;
  profile.competencies[4].value = 100;
  state.profile = profile;
  await page.goto('/profile');
  await expect(skillRegion(page).getByRole('alert')).toBeVisible();
  await expect(skillRegion(page).getByRole('progressbar')).toHaveCount(0);
  state.policy = policyData();
  await page.getByRole('button', { name: 'Повторить загрузку шкал' }).click();
  const expert = page.locator('.skill-card').nth(3);
  await expect(expert.locator('.skill-grade')).toHaveText('Грэйд 4 · Эксперт');
  await expect(expert.locator('.skill-xp')).toHaveText('5 XP');
  await expect(expert.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
  expect(await expert.locator('.expert-dot').count()).toBeGreaterThan(0);
  const stillGrade2 = page.locator('.skill-card').nth(4);
  await expect(stillGrade2.locator('.skill-grade')).toHaveText('Грэйд 2 · Ревьюер−');
  await expect(stillGrade2.locator('.skill-xp')).toHaveText('100 XP');
  await expect(stillGrade2.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
  await expect(stillGrade2.getByRole('progressbar')).not.toHaveAttribute('tabindex');
  await expect(stillGrade2.locator('.expert-dot')).toHaveCount(0);
  profile.competencies[4].grade = 'expert';
  profile.competencies[4].expert_confirmed = true;
  await refreshProfile(page);
  await expect(stillGrade2.locator('.skill-grade')).toHaveText('Грэйд 4 · Эксперт');
  await expect(stillGrade2.locator('.skill-xp')).toHaveText('100 XP');
  await expect(stillGrade2.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
  await expect(stillGrade2.locator('.expert-dots')).toBeVisible();
});

test('expert sparkles move without hover and stay contained through hover and keyboard focus', async ({
  page,
}, info) => {
  await fixture(page);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.goto('/profile');
  const card = page.locator('.expert-card');
  const progress = card.getByRole('progressbar');
  const point = card.locator('.expert-dot').first();
  const track = card.locator('.expert-dots');
  await progress.scrollIntoViewIfNeeded();
  await page.mouse.move(0, 0);
  await expect(track).toHaveCSS('height', '28px');
  await expect(track).toHaveCSS('overflow', 'hidden');
  await expect(point).toHaveCSS('transform', 'none');
  await expect
    .poll(() => sparkleMotion(point))
    .toMatchObject({
      animation: expect.stringMatching(/prodguild-expert-drift/),
      iterations: 'infinite',
    });
  const initialTransform = (await sparkleMotion(point)).transform;
  await expect.poll(async () => (await sparkleMotion(point)).transform).not.toBe(initialTransform);
  await card.screenshot({ path: `/tmp/prodguild-profile-sparkles-${info.project.name}.png` });
  await card.hover({ position: { x: 12, y: 12 } });
  await expect(point).not.toHaveCSS('transform', 'none');
  expect(
    await card.evaluate(element => {
      const cardBox = element.getBoundingClientRect();
      return Array.from(element.querySelectorAll('.expert-dot')).every(dot => {
        const box = dot.getBoundingClientRect();
        return (
          box.left >= cardBox.left &&
          box.right <= cardBox.right &&
          box.top >= cardBox.top &&
          box.bottom <= cardBox.bottom
        );
      });
    }),
  ).toBe(true);
  await page.mouse.move(0, 0);
  await expect(point).toHaveCSS('transform', 'none');
  const afterHoverTransform = (await sparkleMotion(point)).transform;
  await expect
    .poll(async () => (await sparkleMotion(point)).transform)
    .not.toBe(afterHoverTransform);
  await progress.focus();
  await expect(progress).toBeFocused();
  await expect(point).not.toHaveCSS('transform', 'none');
  await expect(progress).toHaveCSS('outline-style', 'solid');
  await expect(progress).toHaveAttribute('aria-valuenow', '1');
  await page.screenshot({
    path: `/tmp/prodguild-profile-sparkles-focus-${info.project.name}.png`,
    fullPage: true,
  });
});

test('reduced motion keeps expert sparkles static on hover and focus', async ({ page }) => {
  await fixture(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/profile');
  const card = page.locator('.expert-card');
  const point = card.locator('.expert-dot').first();
  await card.hover();
  await card.getByRole('progressbar').focus();
  await expect(point).toHaveCSS('transform', 'none');
  await expect(point).toHaveCSS('transition-duration', '0s');
  await expect
    .poll(() => sparkleMotion(point))
    .toMatchObject({ transform: 'none', animation: 'none' });
  await expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
});

test.describe('touch input', () => {
  test.use({ hasTouch: true, isMobile: true });

  test('expert sparkles move continuously on touch without hover', async ({ page }) => {
    await fixture(page);
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.goto('/profile');
    expect(await page.evaluate(() => matchMedia('(hover: none)').matches)).toBe(true);
    const card = page.locator('.expert-card');
    const point = card.locator('.expert-dot').first();
    await card.scrollIntoViewIfNeeded();
    await expect
      .poll(() => sparkleMotion(point))
      .toMatchObject({
        animation: expect.stringMatching(/prodguild-expert-drift/),
        iterations: 'infinite',
      });
    const initialTransform = (await sparkleMotion(point)).transform;
    await expect
      .poll(async () => (await sparkleMotion(point)).transform)
      .not.toBe(initialTransform);
    await card.tap();
    await card.getByRole('progressbar').focus();
    await expect(point).toHaveCSS('transform', 'none');
    await expect(point).toHaveCSS('transition-duration', '0s');
    const focusedTransform = (await sparkleMotion(point)).transform;
    await expect
      .poll(async () => (await sparkleMotion(point)).transform)
      .not.toBe(focusedTransform);
  });
});

test('history pagination retries the requested page without losing current transactions', async ({
  page,
}) => {
  const state = await fixture(page);
  state.history = transactions(20);
  state.history[0].settled_delta = 2;
  state.history[0].reserved_delta = -1;
  state.history[1].settled_delta = -3;
  state.history[2].settled_delta = 0;
  await page.goto('/profile');
  const history = historyRegion(page);
  await expect(history.locator('.transaction')).toHaveCount(20);
  for (const [index, label] of ['+2', '-3', '0'].entries()) {
    const amount = history.locator('.transaction-amount').nth(index);
    await expect(amount.locator('.currency-number')).toHaveText(label);
    await expect(amount.locator('img')).toBeVisible();
  }
  await expect(history.locator('.reserve-change .currency-number')).toHaveText('-1');
  await expect(history.locator('.reserve-change img')).toBeVisible();
  await expect(history.getByRole('button', { name: 'Назад', exact: true })).toBeDisabled();
  state.historyStatus = 503;
  await history.getByRole('button', { name: 'Далее', exact: true }).click();
  await expect(history.getByRole('alert')).toBeVisible();
  await expect(history.locator('.transaction')).toHaveCount(20);
  await expect(history).toContainText('Страница 1');
  state.historyStatus = 200;
  state.history = transactions(1, 20);
  await history.getByRole('button', { name: 'Повторить загрузку истории' }).click();
  await expect(history.locator('.transaction')).toHaveCount(1);
  await expect(history).toContainText('Страница 2');
  await expect(history.getByRole('button', { name: 'Далее', exact: true })).toBeDisabled();
  state.history = transactions(20);
  await history.getByRole('button', { name: 'Назад', exact: true }).click();
  await expect(history).toContainText('Страница 1');
  expect(
    state.requests
      .filter(request => request.path.endsWith('/transactions'))
      .map(request => request.offset),
  ).toEqual(['0', '20', '20', '0']);
});

test('wallet uses one amount line and only displays an actual reserve', async ({ page }, info) => {
  expect(formatProditsAmount(1.256, true)).toBe('+1,26');
  expect(formatProditsAmount(-1.256, true)).toBe('-1,26');
  expect(formatProditsAmount(0.001, true)).toBe('0');
  expect(formatProditsAmount(-0.001)).toBe('0');
  expect(formatProditsAmount(null)).toBe('—');
  const state = await fixture(page);
  await page.goto('/profile');
  const wallet = page.getByRole('region', { name: 'Баланс', exact: true });
  const amount = wallet.locator('.balance app-currency-amount');
  await expect(amount.locator('.currency-number')).toHaveText('10');
  await expect(amount.locator('.currency-unit')).toHaveText('PRODитов');
  await expect(amount.locator('img')).toBeVisible();
  await expect(amount.locator('img')).toHaveAttribute('alt', '');
  await expect(amount.locator('img')).toHaveAttribute('aria-hidden', 'true');
  await expect
    .poll(() => amount.locator('img').evaluate(image => (image as HTMLImageElement).naturalWidth))
    .toBeGreaterThan(0);
  await expect(wallet.locator('.reserve-note')).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  const geometry = await amount.evaluate(element => {
    const image = element.querySelector('img') as HTMLImageElement;
    const imageRect = image.getBoundingClientRect();
    const number = element.querySelector('.currency-number') as HTMLElement;
    const numberRect = number.getBoundingClientRect();
    const font = getComputedStyle(number);
    const context = document.createElement('canvas').getContext('2d');
    if (!context) throw new Error('Canvas font measurement is unavailable');
    context.font = `${font.fontWeight} ${font.fontSize} ${font.fontFamily}`;
    const digits = context.measureText(number.textContent ?? '');
    const marker = document.createElement('span');
    marker.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
    number.append(marker);
    const baseline = marker.getBoundingClientRect().top;
    marker.remove();
    return {
      ratio: imageRect.width / imageRect.height,
      topDifference: imageRect.top - (baseline - digits.actualBoundingBoxAscent),
      bottomDifference: imageRect.bottom - (baseline + digits.actualBoundingBoxDescent),
      numberBeforeIcon: numberRect.right <= imageRect.left,
      unitClipped: getComputedStyle(element.querySelector('.currency-unit') as HTMLElement)
        .clipPath,
    };
  });
  expect(geometry.ratio).toBeCloseTo(100 / 60, 2);
  expect(Math.abs(geometry.topDifference)).toBeLessThan(1);
  expect(Math.abs(geometry.bottomDifference)).toBeLessThan(1);
  expect(geometry.numberBeforeIcon).toBe(true);
  expect(geometry.unitClipped).toBe('inset(50%)');
  state.profile = {
    ...profileData(),
    wallet: { settled: 15, reserved: 5, available: 10, unit: 'PRODиты' },
  };
  await refreshProfile(page);
  await expect(wallet.locator('.reserve-note .currency-number')).toHaveText('5');
  await expect(wallet.locator('.reserve-note img')).toBeVisible();
  await expect(wallet).not.toContainText('Всего на счёте');
  for (const [amount, unit] of [
    [0, 'PRODитов'],
    [1, 'PRODит'],
    [2, 'PRODита'],
    [4, 'PRODита'],
    [11, 'PRODитов'],
    [12, 'PRODитов'],
    [14, 'PRODитов'],
    [21, 'PRODит'],
    [22, 'PRODита'],
    [120, 'PRODитов'],
    [1234567890, 'PRODитов'],
  ] as const) {
    state.profile = {
      ...profileData(),
      wallet: { settled: amount, reserved: 0, available: amount, unit: 'PRODиты' },
    };
    await refreshProfile(page);
    const formatted = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(amount);
    await expect(wallet.locator('.balance .currency-number')).toHaveText(formatted);
    await expect(wallet.locator('.balance .currency-unit')).toHaveText(unit);
    await expect(wallet.locator('.balance app-currency-amount')).toHaveAttribute(
      'title',
      `${formatted} ${unit}`,
    );
    await expect(page.getByTestId('credits-balance')).toHaveAttribute(
      'aria-label',
      `Баланс: ${formatted} ${unit}. История операций`,
    );
  }
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await wallet.screenshot({ path: info.outputPath('wallet-currency.png') });
});

test('sidebar owns wallet and account actions without a page topbar or demo navigation', async ({
  page,
  viewport,
}) => {
  const state = await fixture(page);
  const profile = profileData();
  profile.wallet = {
    settled: 1234567890,
    reserved: 0,
    available: 1234567890,
    unit: 'PRODиты',
  };
  state.profile = profile;
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: 'Профиль', exact: true })).toBeVisible();
  await expect(page.locator('.balance .currency-number')).toHaveText('1 234 567 890');
  await expect(page.locator('.topbar, .header-name')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Результаты демо-работ' })).toHaveCount(0);
  const mobile = Boolean(viewport && viewport.width <= 620);
  if (mobile) {
    const toggle = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
    const [button, heading] = await Promise.all([
      toggle.boundingBox(),
      page.getByRole('heading', { name: 'Профиль', exact: true }).boundingBox(),
    ]);
    expect(button && heading && button.x + button.width <= heading.x).toBe(true);
    expect(button && heading && Math.abs(button.y - heading.y) <= 12).toBe(true);
    expect(heading?.y).toBeLessThan(32);
    await expect(page.locator('.case-content')).toHaveCSS('padding-top', '0px');
    await toggle.click();
  } else {
    await page.getByRole('button', { name: 'Скрыть боковую панель', exact: true }).click();
    await expect(page.locator('app-sidebar')).toHaveCSS('width', '88px');
  }
  const balance = page.getByTestId('credits-balance');
  await expect(balance).toHaveCount(1);
  await expect(balance).toHaveAttribute('href', '/profile#wallet');
  await expect(balance).toHaveAccessibleName('Баланс: 1 234 567 890 PRODитов. История операций');
  await expect(balance.locator('img')).toBeVisible();
  await expect(balance.locator('svg')).toHaveCount(0);
  await expect(balance).toHaveAttribute('title', 'Баланс: 1 234 567 890 PRODитов');
  const currency = balance.locator('app-currency-amount');
  if (mobile) {
    await expect(currency).not.toHaveClass(/compact/);
    await expect(currency.locator('.currency-number')).toBeVisible();
  } else {
    await expect(currency).toHaveClass(/compact/);
    await expect
      .poll(async () => (await currency.locator('.number-clip').boundingBox())?.width)
      .toBe(0);
  }
  const [iconBox, balanceBox] = await Promise.all([
    balance.locator('img').boundingBox(),
    balance.boundingBox(),
  ]);
  expect(
    iconBox &&
      balanceBox &&
      iconBox.x >= balanceBox.x &&
      iconBox.x + iconBox.width <= balanceBox.x + balanceBox.width,
  ).toBe(true);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await expect(
    page.locator('app-sidebar').getByRole('link', { name: 'Профиль', exact: true }),
  ).toBeVisible();
  await balance.focus();
  await expect(balance).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/profile#wallet$/);
  if (mobile) {
    await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toHaveCount(0);
    await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  }
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test('wallet navigation waits for the profile layout and ordinary refresh keeps user scroll', async ({
  page,
  viewport,
}) => {
  const state = await fixture(page);
  const profile = profileData();
  profile.competencies = Array.from({ length: 8 }, (_, index) => ({
    ...profile.competencies[1],
    competency_id: `delayed-skill-${index}`,
    name: `Навык ${index + 1}`,
  }));
  state.profile = profile;
  let release = () => {};
  state.profileGate = new Promise<void>(resolve => (release = resolve));
  await page.goto('/catalog');
  if (viewport && viewport.width <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await page.getByTestId('credits-balance').click();
  await expect(page).toHaveURL(/\/profile#wallet$/);
  await expect(page.getByRole('progressbar', { name: 'Загружаем профиль…' })).toBeVisible();
  await expect(historyRegion(page)).toContainText('Приветственный бонус');
  release();
  await expect(page.locator('.skill-card')).toHaveCount(8);
  await expect
    .poll(() =>
      page.locator('#wallet').evaluate(element => {
        const box = element.getBoundingClientRect();
        return box.top >= 0 && box.bottom <= innerHeight;
      }),
    )
    .toBe(true);

  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  state.profileGate = new Promise<void>(resolve => (release = resolve));
  await refreshProfile(page);
  await expect(page.getByRole('progressbar', { name: 'Загружаем профиль…' })).toBeVisible();
  release();
  await expect(page.getByRole('progressbar', { name: 'Загружаем профиль…' })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
  await expect(page).toHaveURL(/\/profile#wallet$/);
});
