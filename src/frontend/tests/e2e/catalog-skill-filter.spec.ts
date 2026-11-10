import { expect, Page, test } from '@playwright/test';

const sql = '10000000-0000-4000-8000-000000000001';
const python = '10000000-0000-4000-8000-000000000002';
const design = '10000000-0000-4000-8000-000000000003';

async function fixture(page: Page, count = 45) {
  const requests: URL[] = [];
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({ token: 'filter-test', email: 'filter@example.com', name: 'Участник' }),
    ),
  );
  await page.route('**/auth/**', route =>
    route.fulfill({ json: { id: 'filter-user', role: 'user', capabilities: ['participant'] } }),
  );
  const cases = Array.from({ length: count }, (_, index) => ({
    id: `case-${index}`,
    title: `Практика ${index}`,
    description: 'Проверьте решение.',
    expected_result: 'Верный результат',
    difficulty: index % 2 ? 'review_plus' : 'review_minus',
    content_version: 1,
    competencies: (index < 30 ? [sql, python] : [[sql, python, design][index % 3]]).map(
      competency_id => ({
        competency_id,
        required_value: index % 2 ? 50 : 20,
        weight: 0.1,
      }),
    ),
  }));
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== 'GET') throw new Error('Filters must only read data');
    if (url.pathname.endsWith('/review-mode'))
      return route.fulfill({ json: { enabled: false, status: 'disabled', can_review: false } });
    if (url.pathname.endsWith('/me/wallet'))
      return route.fulfill({ json: { settled: 10, available: 10, reserved: 0, unit: 'credit' } });
    if (url.pathname.endsWith('/competencies'))
      return route.fulfill({
        json: [
          { id: sql, name: 'SQL', description: '' },
          { id: python, name: 'Python', description: '' },
          { id: design, name: 'Дизайн', description: '' },
        ],
      });
    if (url.pathname === '/api/v1/cases') {
      requests.push(url);
      const ids = (url.searchParams.get('competency_ids') ?? '').split(',').filter(Boolean);
      const level = url.searchParams.get('difficulty');
      if (level && !['review_minus', 'review_plus', 'expert'].includes(level))
        return route.fulfill({ status: 422, json: { detail: 'Unsupported difficulty' } });
      const query = url.searchParams.get('q') ?? '';
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 20);
      return route.fulfill({
        json: cases
          .filter(
            item =>
              (!ids.length ||
                ids.some(id => item.competencies.some(skill => skill.competency_id === id))) &&
              (!level || item.difficulty === level) &&
              item.title.toLocaleLowerCase('ru').includes(query),
          )
          .slice(offset, offset + limit),
      });
    }
    return route.fulfill({ json: [] });
  });
  return requests;
}

test('alphabetical skills use OR across pages and survive reload, back, level and search changes', async ({
  page,
}, info) => {
  const requests = await fixture(page);
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows).toHaveCount(20);
  const picker = page.getByRole('button', { name: /^Навыки/ });
  await picker.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Навыки', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.option')).toHaveText(['Дизайн', 'Python', 'SQL']);
  await dialog.evaluate(async element => {
    const animations = element.closest('tui-dropdown')?.getAnimations({ subtree: true }) ?? [];
    await Promise.all(
      animations
        .filter(animation => animation.effect?.getComputedTiming().endTime !== Infinity)
        .map(animation => animation.finished.catch(() => undefined)),
    );
  });
  const search = dialog.getByRole('searchbox', { name: 'Найти навык' });
  await search.click();
  await search.pressSequentially('sql');
  await expect(dialog.getByRole('checkbox')).toHaveCount(1);
  await dialog.getByRole('checkbox', { name: 'SQL', exact: true }).check();
  await expect(rows).toHaveCount(20);
  await search.fill('');
  await dialog.getByRole('checkbox', { name: 'Python', exact: true }).check();
  await expect(rows).toHaveCount(20);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(picker).toBeFocused();
  await expect(page.getByText('Любой из выбранных', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Далее', exact: true }).click();
  await expect(rows).toHaveCount(20);
  await expect(rows.filter({ hasText: 'Практика 30' })).toBeVisible();
  await expect(rows.filter({ hasText: 'Практика 31' })).toBeVisible();
  await expect(rows.filter({ hasText: 'Практика 32' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Далее', exact: true })).toBeDisabled();
  expect(requests.at(-1)?.searchParams.get('limit')).toBe('21');
  await expect(page).toHaveURL(/offset=20/);
  await page.reload();
  await expect(rows).toHaveCount(20);
  await expect(page.getByRole('button', { name: 'Убрать навык SQL', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Уровень 2', exact: true }).click();
  await expect(rows).toHaveCount(20);
  expect(new URL(page.url()).searchParams.has('offset')).toBe(false);
  expect(requests.at(-1)?.searchParams.get('competency_ids')).toBe(`${sql},${python}`);
  await page.goBack();
  await expect(rows).toHaveCount(20);
  const query = page.getByRole('searchbox', { name: 'Поиск по названию кейса' });
  await query.click();
  await query.pressSequentially('практика 4');
  await expect(rows).toHaveCount(4);
  expect(requests.at(-1)?.searchParams.get('offset')).toBe('0');
  await page.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click();
  await expect(rows).toHaveCount(20);
  await expect(query).toHaveValue('');
  expect(new URL(page.url()).searchParams.has('competency_ids')).toBe(false);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath('catalog-skill-filter.png'), fullPage: true });
});

test('latest filter wins when an earlier response arrives late', async ({ page }) => {
  await fixture(page);
  let release = () => {};
  const held = new Promise<void>(resolve => (release = resolve));
  await page.route('**/api/v1/cases?**', async route => {
    if (new URL(route.request().url()).searchParams.get('q') !== 'old') return route.fallback();
    await held;
    return route.fulfill({ json: [] });
  });
  await page.goto('/catalog');
  const search = page.getByRole('searchbox', { name: 'Поиск по названию кейса' });
  await search.fill('old');
  await expect(page).toHaveURL(/q=old/);
  await search.fill('практика 44');
  await expect(page.getByRole('link', { name: /Практика 44/ })).toBeVisible();
  const oldResponse = page.waitForResponse(
    response => new URL(response.url()).searchParams.get('q') === 'old',
  );
  release();
  await (await oldResponse).finished();
  await expect(page.getByRole('link', { name: /Практика 44/ })).toBeVisible();
});

test('login restores the catalog query and selected skills', async ({ page }) => {
  await fixture(page);
  await page.addInitScript(() => sessionStorage.removeItem('prodguild.api.session.v1'));
  await page.route('**/auth/sign_in/email', route =>
    route.fulfill({
      json: { access_token: 'filter-test', token_type: 'bearer' },
    }),
  );
  const query = new URLSearchParams({
    q: 'практика',
    competency_ids: sql + ',' + python,
    offset: '20',
  });
  await page.goto('/login?returnUrl=' + encodeURIComponent('/catalog?' + query.toString()));
  await page.getByLabel('Email', { exact: true }).fill('filter@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('filter-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(
    url => url.pathname === '/catalog' && url.searchParams.toString() === query.toString(),
  );
  await expect(page.getByRole('button', { name: 'Убрать навык SQL', exact: true })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Убрать навык Python', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link'),
  ).toHaveCount(20);
});

test('three master difficulty levels filter and ignore unsupported URL values', async ({
  page,
}) => {
  await fixture(page);
  const requests: URL[] = [];
  const levels = [
    ['review_minus', 'Уровень 1'],
    ['review_plus', 'Уровень 2'],
    ['expert', 'Уровень 3'],
  ];
  await page.route('**/api/v1/cases?**', route => {
    const url = new URL(route.request().url());
    requests.push(url);
    const difficulty = url.searchParams.get('difficulty');
    if (difficulty && !levels.some(([value]) => value === difficulty))
      return route.fulfill({ status: 422, json: { detail: 'Unsupported difficulty' } });
    return route.fulfill({
      json: levels
        .filter(([value]) => !difficulty || value === difficulty)
        .map(([value, label]) => ({
          id: value,
          title: `Практика: ${label}`,
          description: 'Проверьте решение.',
          expected_result: 'Результат',
          difficulty: value,
          content_version: 1,
          competencies: [],
        })),
    });
  });
  await page.goto('/catalog');
  const filters = page.getByRole('group', { name: 'Уровень кейса', exact: true });
  await expect(filters.getByRole('button')).toHaveText([
    'Все уровни',
    'Уровень 1',
    'Уровень 2',
    'Уровень 3',
  ]);
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows).toHaveCount(3);
  for (const [value, label] of levels) {
    await filters.getByRole('button', { name: label, exact: true }).click();
    await expect(page).toHaveURL(url => url.searchParams.get('difficulty') === value);
    await expect(rows).toHaveCount(1);
    await expect(rows.getByText(label, { exact: true })).toBeVisible();
  }
  await page.goto('/catalog?difficulty=performer');
  await expect(filters.getByRole('button', { name: 'Все уровни', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(rows).toHaveCount(3);
  expect(requests.at(-1)?.searchParams.has('difficulty')).toBe(false);
  expect(requests.some(url => url.searchParams.get('difficulty') === 'performer')).toBe(false);
});

for (const count of [20, 21, 40, 41]) {
  test(`catalog paginates ${count} results without an empty final page`, async ({ page }) => {
    const requests = await fixture(page, count);
    await page.goto('/catalog');
    const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
    const next = page.getByRole('button', { name: 'Далее', exact: true });
    const seen: string[] = [];
    for (let offset = 0; offset < count; offset += 20) {
      await expect(rows).toHaveCount(Math.min(20, count - offset));
      await expect(rows.first()).toHaveAttribute('href', `/cases/case-${offset}`);
      seen.push(
        ...(await rows.evaluateAll(links => links.map(link => link.getAttribute('href') ?? ''))),
      );
      expect(requests.at(-1)?.searchParams.get('limit')).toBe('21');
      expect(requests.at(-1)?.searchParams.get('offset')).toBe(String(offset));
      if (offset + 20 < count) {
        await expect(next).toBeEnabled();
        await next.click();
      } else if (offset) {
        await expect(next).toBeDisabled();
      } else {
        await expect(next).toHaveCount(0);
      }
    }
    expect(new Set(seen).size).toBe(count);
  });
}

test('failed next page keeps its rows and retries the requested offset', async ({ page }) => {
  await fixture(page);
  let fail = true;
  await page.route('**/api/v1/cases?**', route => {
    if (new URL(route.request().url()).searchParams.get('offset') !== '20' || !fail)
      return route.fallback();
    return route.fulfill({ status: 503, json: {} });
  });
  await page.goto('/catalog');
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  await expect(rows.first()).toHaveAttribute('href', '/cases/case-0');
  await page.getByRole('button', { name: 'Далее', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(rows.first()).toHaveAttribute('href', '/cases/case-0');
  fail = false;
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await expect(rows.first()).toHaveAttribute('href', '/cases/case-20');
  await expect(page).toHaveURL(/offset=20/);
  await expect(page.getByRole('alert')).toHaveCount(0);
});
