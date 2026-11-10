import { expect, Page, test } from '@playwright/test';

const root = '/reviews';
const work = '/reviews/catalog-search';
const key = 'prodguild.reviews-demo.v1.demo.catalog-search';
const code = (page: Page, path = 'src/search.ts') =>
  page.getByRole('textbox', { name: `Редактор: ${path}`, exact: true });

async function demo(page: Page, path = work): Promise<void> {
  await page.route('**/auth/sign_in/email', route =>
    route.fulfill({ json: { access_token: 'review-test-session', token_type: 'bearer' } }),
  );
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'demo', role: 'user', capabilities: [] } }),
  );
  await page.route('**/auth/sign_out', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/me/wallet', route =>
    route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } }),
  );
  await page.route('**/api/v1/competencies*', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/cases?*', route => route.fulfill({ json: [] }));
  await page.goto(path);
  await expect(page).toHaveURL(/\/login\?returnUrl=/);
  await page.getByRole('button', { name: 'Ревьюер+', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto(path);
  await expect(page).toHaveURL(path);
}

async function noOverflow(page: Page): Promise<void> {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
}

test('три примера открываются по прямому URL, возврату и истории без ошибок', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await demo(page, root);
  const list = page.getByRole('region', { name: 'Работы на ревью' });
  await expect(list.getByRole('link')).toHaveCount(3);
  await list.getByRole('link', { name: 'Открыть ревью: Поиск по каталогу', exact: true }).click();
  await expect(code(page)).toBeVisible();
  await page.getByRole('button', { name: 'Обзор', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Обзор решения' })).toContainText(
    'Вынес фильтрацию',
  );
  await page.getByRole('button', { name: 'Посмотреть код', exact: true }).click();
  await expect(code(page)).toBeVisible();
  await page.getByRole('link', { name: 'Все работы', exact: true }).click();
  await expect(list).toBeVisible();
  await list
    .getByRole('link', { name: 'Открыть ревью: Почему пользователи уходят?', exact: true })
    .click();
  await expect(code(page, 'analysis/retention.py')).toBeVisible();
  await page.reload();
  await expect(code(page, 'analysis/retention.py')).toContainText('retention_d30');
  await page.goBack();
  await expect(list).toBeVisible();
  await list.getByRole('link', { name: 'Открыть ревью: Понятная подписка', exact: true }).click();
  await expect(code(page, 'src/pricing.js')).toContainText('calculatePrice');
  await noOverflow(page);
  expect(errors).toEqual([]);
});

test('настоящий ввод, Undo, смена файлов и обзор сохраняют правки отдельно от исходника', async ({
  page,
}) => {
  await demo(page);
  const editor = code(page);
  await expect(editor).toBeVisible();
  const original = await editor.innerText();
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('const reviewed = true;');
  await expect(editor).toHaveText('const reviewed = true;');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await page.getByRole('button', { name: 'Исходник', exact: true }).click();
  await expect(editor).toHaveAttribute('aria-readonly', 'true');
  await expect.poll(() => editor.innerText()).toBe(original);
  await page.getByRole('button', { name: 'Мои правки', exact: true }).click();
  await expect(editor).toHaveText('const reviewed = true;');
  await page
    .getByRole('navigation', { name: 'Файлы на ревью' })
    .getByRole('button', { name: 'README.md', exact: true })
    .click();
  await expect(code(page, 'README.md')).toBeVisible();
  await page.getByRole('button', { name: 'Открытый файл src/search.ts', exact: true }).click();
  await expect(editor).toHaveText('const reviewed = true;');
  await page.getByRole('button', { name: 'Обзор', exact: true }).click();
  await page.getByRole('button', { name: 'Посмотреть код', exact: true }).click();
  await expect(editor).toHaveText('const reviewed = true;');
  await editor.click();
  await editor.press('ControlOrMeta+End');
  await editor.pressSequentially(' // note');
  await editor.press('ControlOrMeta+z');
  await expect(editor).not.toHaveText('const reviewed = true; // note');
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('const saved = 42;');
  await page.reload();
  await expect(editor).toHaveText('const saved = 42;');
  await page.getByRole('button', { name: 'Исходник', exact: true }).click();
  await expect.poll(() => editor.innerText()).toBe(original);
  await noOverflow(page);
});

test('комментарии привязаны к исходнику, поддерживают ответы, решение и сохранение', async ({
  page,
}) => {
  await demo(page);
  const discussion = page.getByRole('complementary', { name: 'Обсуждения к файлу' });
  const text = page.getByLabel('Новый комментарий', { exact: true });
  await page.getByRole('button', { name: /^Обсуждения/ }).click();
  await expect(text).toBeFocused();
  await text.fill('   ');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Введите комментарий');
  await page.getByLabel('Строка исходника').fill('999');
  await text.fill('Проверка диапазона строки.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Выберите строку');
  await page.getByLabel('Строка исходника').fill('9');
  await text.fill('Нужна обработка пробелов и тест пустого запроса.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  const thread = discussion.getByRole('article').filter({ hasText: 'Нужна обработка пробелов' });
  await expect(thread).toContainText('Ревьюер+');
  await expect(thread.locator('code')).toContainText('const normalized');
  await thread.getByRole('button', { name: 'Ответить', exact: true }).click();
  await page
    .getByLabel('Ответ в обсуждение', { exact: true })
    .fill('Добавлю проверку граничных значений.');
  await page.getByRole('button', { name: 'Добавить ответ', exact: true }).click();
  await expect(thread).toContainText('Добавлю проверку');
  await thread.getByRole('button', { name: 'Решить обсуждение строки 9', exact: true }).click();
  await expect(thread).toContainText('Решено');
  await page.reload();
  await expect(thread).toContainText('Решено');
  await expect(thread).toContainText('Добавлю проверку');
  await thread
    .getByRole('button', { name: 'Открыть снова обсуждение строки 9', exact: true })
    .click();
  await expect(thread).toContainText('Открыто');
  await page
    .getByRole('navigation', { name: 'Файлы на ревью' })
    .getByRole('button', { name: 'README.md', exact: true })
    .click();
  await expect(discussion.getByText('Нужна обработка пробелов', { exact: false })).toHaveCount(0);
  await noOverflow(page);
});

test('черновики комментариев не теряются при переключении файлов, длинный текст не расширяет страницу', async ({
  page,
}) => {
  await demo(page);
  await page.getByLabel('Новый комментарий', { exact: true }).fill('Черновик к поиску');
  await page
    .getByRole('article', { name: 'Обсуждение строки 9', exact: true })
    .getByRole('button', { name: 'Ответить', exact: true })
    .click();
  await page.getByLabel('Ответ в обсуждение', { exact: true }).fill('Ответ без потери черновика.');
  await page.getByRole('button', { name: 'Добавить ответ', exact: true }).click();
  await expect(page.getByLabel('Новый комментарий', { exact: true })).toHaveValue(
    'Черновик к поиску',
  );
  const tree = page.getByRole('navigation', { name: 'Файлы на ревью' });
  await tree.getByRole('button', { name: 'README.md', exact: true }).click();
  await page.getByLabel('Новый комментарий', { exact: true }).fill('Комментарий'.repeat(100));
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await tree.getByRole('button', { name: 'src/search.ts', exact: true }).click();
  await expect(page.getByLabel('Новый комментарий', { exact: true })).toHaveValue(
    'Черновик к поиску',
  );
  await noOverflow(page);
});

test('данные изолированы по аккаунту, неизвестная работа не создаёт запись', async ({ page }) => {
  await demo(page);
  await code(page).fill('const privateReview = true;');
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: 'another-reviewer', role: 'user', capabilities: [] } }),
  );
  await page.route('**/api/v1/me/wallet', route =>
    route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } }),
  );
  await page.evaluate(() =>
    sessionStorage.setItem(
      'prodguild.api.session.v1',
      JSON.stringify({
        token: 'review-test-session',
        email: 'reviewer@example.com',
        name: 'Новый ревьюер',
      }),
    ),
  );
  await page.goto(work);
  await expect(code(page)).toContainText('export interface Product');
  await expect(code(page)).not.toContainText('privateReview');
  const before = await page.evaluate(() =>
    Object.keys(localStorage).filter(key => key.startsWith('prodguild.reviews-demo.v1.')),
  );
  await page.goto('/reviews/unknown-review');
  await expect(page.getByRole('heading', { name: 'Работа не найдена', exact: true })).toBeVisible();
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter(key => key.startsWith('prodguild.reviews-demo.v1.')),
    ),
  ).toEqual(before);
});

test('сбой хранилища сохраняет работу в сессии и повреждённые данные открывают исходный пример', async ({
  page,
}) => {
  await demo(page);
  await page.evaluate(key => localStorage.setItem(key, '{broken'), key);
  await page.reload();
  await expect(code(page)).toContainText('export interface Product');
  await expect(
    page.getByText('Не удалось восстановить демо-ревью. Показан исходный пример.', { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new Error('Blocked');
    };
  });
  await code(page).fill('const memoryOnly = true;');
  await expect(code(page)).toHaveText('const memoryOnly = true;');
  await expect(page.getByText(/Хранилище недоступно/)).toBeVisible();
  await page
    .getByLabel('Новый комментарий', { exact: true })
    .fill('Комментарий остаётся в памяти.');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(
    page.getByRole('article').filter({ hasText: 'Комментарий остаётся в памяти.' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Все работы', exact: true }).click();
  await page.getByRole('link', { name: 'Открыть ревью: Поиск по каталогу', exact: true }).click();
  await expect(code(page)).toHaveText('const memoryOnly = true;');
});
