import { expect, Response, test } from '@playwright/test';

test.use({ actionTimeout: 15000 });

test('catalog reads master filters and real pagination without business writes', async ({
  page,
}, info) => {
  test.setTimeout(120000);
  const password = process.env['PRODGUILD_E2E_PASSWORD'];
  if (!password) throw new Error('Set PRODGUILD_E2E_PASSWORD for the isolated C1 participant.');
  const query = '[Recovery C1 Pagination]';
  const writes: string[] = [];
  const errors: string[] = [];
  const requests: string[] = [];
  const evidence: { url: string; status: number; ids: string[] }[] = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/') && request.method() !== 'GET')
      writes.push(`${request.method()} ${url.pathname}`);
    if (url.pathname === '/api/v1/cases') requests.push(url.search);
  });
  page.on('pageerror', error => errors.push(error.message));
  const rows = page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link');
  const next = page.getByRole('button', { name: 'Далее', exact: true });
  const search = page.getByRole('searchbox', { name: 'Поиск по названию кейса' });

  async function verifyPage(response: Response) {
    expect(response.status()).toBe(200);
    const data: unknown = await response.json();
    if (!Array.isArray(data)) throw new Error('Catalog must return an array.');
    const cases = data.map((value: unknown) => {
      if (
        !value ||
        typeof value !== 'object' ||
        !('id' in value) ||
        typeof value.id !== 'string' ||
        !('title' in value) ||
        typeof value.title !== 'string'
      )
        throw new Error('Invalid catalog record.');
      return { id: value.id, title: value.title };
    });
    await expect(rows).toHaveCount(Math.min(20, cases.length));
    await expect
      .poll(() => rows.evaluateAll(links => links.map(link => link.getAttribute('href'))))
      .toEqual(cases.slice(0, 20).map(item => `/cases/${item.id}`));
    const url = new URL(response.url());
    expect(url.searchParams.get('limit')).toBe('21');
    evidence.push({
      url: url.pathname + url.search,
      status: response.status(),
      ids: cases.map(item => item.id),
    });
    return cases;
  }

  async function change(action: () => Promise<unknown>, match: (url: URL) => boolean) {
    const response = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/v1/cases' && match(url);
    });
    await action();
    return verifyPage(await response);
  }

  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill('performer.local@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/, { timeout: 30000 });
  await expect(page.getByRole('heading', { name: 'Каталог кейсов', exact: true })).toBeVisible();
  const first = await change(
    async () => {
      await search.click();
      await search.pressSequentially(query);
    },
    url => url.searchParams.get('q') === query.toLowerCase(),
  );
  expect(first).toHaveLength(21);
  await expect(next).toBeEnabled();
  await page.screenshot({ path: info.outputPath('catalog-page-1.png'), fullPage: true });
  const second = await change(
    () => next.click(),
    url => url.searchParams.get('offset') === '20',
  );
  expect(second.map(item => item.id)).toEqual([first[20].id]);
  await expect(next).toBeDisabled();
  await page.screenshot({ path: info.outputPath('catalog-page-2.png'), fullPage: true });
  await change(
    () => page.reload(),
    url => url.searchParams.get('offset') === '20',
  );
  await change(
    () => page.goBack(),
    url => url.searchParams.get('offset') === '0',
  );

  // This record was only the lookahead on the first page, never a visible local row.
  const found = await change(
    () => search.fill(first[20].title),
    url => url.searchParams.get('q') === first[20].title.toLowerCase(),
  );
  expect(found.map(item => item.id)).toEqual([first[20].id]);
  await change(
    () => search.fill(query),
    url => url.searchParams.get('q') === query.toLowerCase(),
  );

  const picker = page.getByRole('button', { name: /^Навыки/ });
  await picker.click();
  const dialog = page.getByRole('dialog', { name: 'Навыки', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.evaluate(async element => {
    const animations = element.closest('tui-dropdown')?.getAnimations({ subtree: true }) ?? [];
    await Promise.all(
      animations
        .filter(animation => animation.effect?.getComputedTiming().endTime !== Infinity)
        .map(animation => animation.finished.catch(() => undefined)),
    );
  });
  await page.screenshot({ path: info.outputPath('catalog-skill-picker.png'), fullPage: true });
  const sqlOnly = await change(
    () => dialog.getByRole('checkbox', { name: 'SQL', exact: true }).check(),
    url => !!url.searchParams.get('competency_ids'),
  );
  expect(sqlOnly).toHaveLength(14);
  const both = await change(
    () => dialog.getByRole('checkbox', { name: 'Python', exact: true }).check(),
    url => (url.searchParams.get('competency_ids') ?? '').split(',').length === 2,
  );
  expect(both).toHaveLength(21);
  expect(both.map(item => item.id)).toEqual(first.map(item => item.id));
  await page.keyboard.press('Escape');
  await expect(page.getByText('Любой из выбранных', { exact: true })).toBeVisible();
  for (const [value, label] of [
    ['review_minus', 'Уровень 1'],
    ['review_plus', 'Уровень 2'],
    ['expert', 'Уровень 3'],
  ]) {
    const filtered = await change(
      () => page.getByRole('button', { name: label, exact: true }).click(),
      url => url.searchParams.get('difficulty') === value,
    );
    expect(filtered).toHaveLength(7);
    await expect(rows.locator('.case-meta > span:first-child')).toHaveText(Array(7).fill(label));
    expect(new URL(page.url()).searchParams.has('offset')).toBe(false);
  }
  await page.screenshot({ path: info.outputPath('catalog-or-level.png'), fullPage: true });
  await change(
    () => page.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click(),
    url =>
      !url.searchParams.has('q') &&
      !url.searchParams.has('difficulty') &&
      !url.searchParams.has('competency_ids'),
  );
  await expect(search).toHaveValue('');
  await change(
    () => page.goto(`/catalog?q=${encodeURIComponent(query)}&difficulty=performer`),
    url => url.searchParams.get('q') === query.toLowerCase(),
  );
  await expect(page.getByRole('button', { name: 'Все уровни', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(requests.some(query => new URLSearchParams(query).get('difficulty') === 'performer')).toBe(
    false,
  );
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
  await info.attach('catalog-api-evidence', {
    body: JSON.stringify(evidence, null, 2),
    contentType: 'application/json',
  });
});
