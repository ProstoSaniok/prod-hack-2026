import { expect, test } from '@playwright/test';

test('неизвестный кейс открывает понятное состояние и не создаёт работу', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/cases/missing-case');
  await expect(page.getByRole('heading', { level: 1, name: 'Кейс не найден' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Перейти к кейсам', exact: true })).toHaveAttribute(
    'href',
    '/catalog',
  );
  await page.evaluate(() => localStorage.setItem('lotty.mock.session.v1', JSON.stringify('demo')));
  await page.goto('/cases/missing-case/work');
  await expect(page).toHaveURL(/\/cases\/missing-case\/work$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Кейс не найден' })).toBeVisible();
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Завершить кейс', exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem('lotty.mock.work.v1.demo'))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('prodguild.solutions.v1'))).toBeNull();
  await page.getByRole('link', { name: 'Перейти к кейсам', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(
    page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link'),
  ).toHaveCount(6);
  expect(errors).toEqual([]);
});

test('недоступная запись в хранилище оставляет демо и черновик рабочими в текущей сессии', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(Storage.prototype, 'setItem', {
      configurable: true,
      value: () => {
        throw new DOMException('Хранилище заполнено', 'QuotaExceededError');
      },
    });
  });

  await page.goto('/login');
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.locator('.storage-notice')).toContainText(
    'Не удалось сохранить данные в браузере',
  );
  await page
    .getByRole('link', { name: 'Продолжить: Почему пользователи уходят?', exact: true })
    .click();
  await expect(page).toHaveURL(/\/cases\/customer-retention\/work$/);
  const solution =
    'Проверяю, как сложность первого перевода влияет на удержание. Предлагаю сравнить две версии сценария и оценить возврат клиентов.';
  const draft = page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true });
  await draft.click();
  await draft.fill(solution);
  await expect(draft).toHaveText(solution);
  await page.getByRole('checkbox').nth(1).check();
  await expect(page.getByText('Черновик в текущей сессии', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Условия кейса', exact: true }).click();
  await page.getByRole('link', { name: /Продолжить кейс/ }).click();
  await expect(draft).toHaveText(solution);
  await expect(page.getByRole('checkbox').nth(1)).toBeChecked();
  await expect(page.locator('.storage-notice')).toContainText(
    'Они доступны до обновления страницы',
  );
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page
    .getByRole('link', { name: 'Продолжить: Почему пользователи уходят?', exact: true })
    .click();
  await expect(draft).toHaveText(solution);
  await expect(page.getByRole('checkbox').nth(1)).toBeChecked();
  expect(await page.evaluate(() => localStorage.getItem('lotty.mock.work.v1.demo'))).toBeNull();
  expect(errors).toEqual([]);
});

test('повреждённый JSON прогресса не мешает открыть кабинет и начать новый кейс', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('lotty.mock.session.v1', JSON.stringify('demo'));
    localStorage.setItem('lotty.mock.work.v1.demo', '{broken');
  });

  await page.goto('/my-cases');
  await expect(
    page.getByRole('heading', { level: 1, name: 'В работе', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Нет начатых кейсов' })).toBeVisible();
  await expect(page.locator('.storage-notice')).toContainText(
    'Хранилище браузера недоступно или повреждено',
  );
  await page.getByRole('link', { name: 'Перейти в каталог', exact: true }).click();
  await page
    .getByRole('link', { name: 'Открыть кейс: Сервис личных финансов', exact: true })
    .click();
  await page.getByRole('link', { name: /Начать кейс/ }).click();
  await expect(page).toHaveURL(/\/cases\/personal-finance\/work$/);
  await expect(
    page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true }),
  ).toHaveText('');
  await expect(page.getByRole('checkbox')).toHaveCount(3);
  await expect(page.getByRole('checkbox').nth(0)).not.toBeChecked();
  expect(errors).toEqual([]);
});
