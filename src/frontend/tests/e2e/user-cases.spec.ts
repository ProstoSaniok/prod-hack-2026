import { expect, Page, test } from '@playwright/test';

const password = 'Practice2026!';
const solution =
  'Выбираю удержание на тридцатый день основной метрикой. Проверяю гипотезу о сложности первого перевода с помощью эксперимента.';

async function fillRegistration(page: Page, login = 'learner', name = 'Анна Практикова') {
  await page.getByLabel('Ваше имя', { exact: true }).fill(name);
  await page.getByLabel('Логин', { exact: true }).fill(login);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByLabel('Повторите пароль', { exact: true }).fill(password);
}

async function register(page: Page, login = 'learner', name = 'Анна Практикова') {
  await page.goto('/register');
  await fillRegistration(page, login, name);
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
}

async function logout(page: Page) {
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
}

async function login(page: Page, username = 'learner') {
  await page.getByLabel('Логин', { exact: true }).fill(username);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
}

async function navigate(page: Page, label: string) {
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page
    .getByRole('navigation', { name: 'Основная навигация' })
    .getByRole('link', { name: label, exact: true })
    .click();
  await expect(page.getByRole('heading', { level: 1, name: label, exact: true })).toBeVisible();
}

test('регистрация валидирует поля, совпадение паролей и сохраняет форму без перезагрузки', async ({
  page,
}) => {
  await page.goto('/register');
  await page.evaluate(() => (document.documentElement.dataset['formSession'] = 'active'));
  const submit = page.getByRole('button', { name: 'Зарегистрироваться', exact: true });
  const name = page.getByLabel('Ваше имя', { exact: true });
  const username = page.getByLabel('Логин', { exact: true });
  const secret = page.getByLabel('Пароль', { exact: true });
  const repeat = page.getByLabel('Повторите пароль', { exact: true });
  await submit.click();
  await expect(username).toHaveAttribute('aria-invalid', 'true');
  await expect(username).toHaveAccessibleDescription('Заполните это поле.');
  await name.fill('   ');
  await username.fill('русский логин');
  await secret.fill('short');
  await repeat.fill('different');
  await submit.click();
  await expect(name).toHaveAccessibleDescription('Укажите имя от 1 до 60 символов.');
  await expect(username).toHaveAccessibleDescription(/От 3 до 32 символов/);
  await expect(secret).toHaveAccessibleDescription('Пароль должен содержать от 8 до 128 символов.');
  await fillRegistration(page);
  await repeat.fill('AnotherPassword!');
  await submit.click();
  await expect(repeat).toHaveAccessibleDescription('Пароли не совпадают.');
  await expect(page).toHaveURL(/\/register$/);
  await repeat.fill(password);
  await page.getByRole('button', { name: 'Показать пароль', exact: true }).click();
  await expect(secret).toHaveAttribute('type', 'text');
  await expect(repeat).toHaveAttribute('type', 'text');
  await page.getByRole('button', { name: 'Скрыть пароль', exact: true }).click();
  await secret.click();
  await expect(secret).toBeFocused();
  await submit.click();
  await expect(page).toHaveURL(/\/catalog$/);
  await expect(page.locator('html')).toHaveAttribute('data-form-session', 'active');
  await navigate(page, 'В работе');
  await expect(page.getByRole('heading', { name: 'Нет начатых кейсов' })).toBeVisible();
  await navigate(page, 'Ревью');
  await expect(page.getByRole('region', { name: 'Работы на ревью' }).getByRole('link')).toHaveCount(
    3,
  );
});

test('занятый логин, неверный пароль, выход и повторный вход без учёта регистра', async ({
  page,
}) => {
  await register(page);
  await logout(page);
  await page.goto('/register');
  await fillRegistration(page, 'LEARNER');
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText(
    'Этот логин уже занят. Выберите другой или войдите.',
  );
  await page.locator('.switch-mode').getByRole('link', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible();
  await page.getByLabel('Логин', { exact: true }).fill('LEARNER');
  await page.getByLabel('Пароль', { exact: true }).fill('IncorrectPassword!');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Неверный логин или пароль');
  await page.reload();
  await login(page, 'LEARNER');
  await page.reload();
  await expect(page).toHaveURL(/\/catalog$/);
  await navigate(page, 'В работе');
  await expect(page.getByRole('heading', { name: 'Нет начатых кейсов' })).toBeVisible();
  await logout(page);
  await page.goto('/my-cases');
  await expect(page).toHaveURL(/\/login\?returnUrl=/);
});

test('защищённая история сохраняет адрес при переходе с входа на регистрацию', async ({ page }) => {
  await page.goto('/reviews');
  await expect(page).toHaveURL(/\/login\?returnUrl=%2Freviews$/);
  await page.locator('.switch-mode').getByRole('link', { name: 'Зарегистрироваться' }).click();
  await expect(page).toHaveURL(/\/register\?returnUrl=%2Freviews$/);
  await fillRegistration(page);
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Ревью', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('region', { name: 'Работы на ревью' }).getByRole('link')).toHaveCount(
    3,
  );
  const menu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await menu.isVisible()) await menu.click();
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  await expect(navigation.getByRole('link', { name: 'Ревью', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
  await expect(navigation.getByRole('link', { name: 'Завершённые', exact: true })).toHaveCount(0);
  await navigation.getByRole('link', { name: 'Каталог кейсов', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
});

test('поиск по навыку работает вместе с направлением и сложностью, сброс возвращает все кейсы', async ({
  page,
}) => {
  await page.goto('/catalog');
  const cards = page.getByRole('region', { name: 'Кейсы', exact: true });
  await expect(cards.getByRole('link')).toHaveCount(6);
  const search = page.getByRole('searchbox', { name: 'Поиск по названию или навыку' });
  await search.click();
  await search.pressSequentially('  JAVASCRIPT  ');
  await page
    .getByRole('group', { name: 'Направление кейса' })
    .getByRole('button', { name: 'Разработка' })
    .click();
  await page.getByRole('combobox', { name: 'Уровень сложности' }).click();
  await page.getByRole('option', { name: 'Начальный', exact: true }).click();
  await expect(page.getByRole('listbox', { name: 'Уровни сложности' })).toBeHidden();
  await expect(cards.getByRole('link')).toHaveCount(1);
  await expect(
    cards.getByRole('heading', { name: 'Поиск по каталогу', exact: true }),
  ).toBeVisible();
  await page.getByRole('combobox', { name: 'Уровень сложности' }).click();
  await page.getByRole('option', { name: 'Средний', exact: true }).click();
  await expect(page.getByRole('listbox', { name: 'Уровни сложности' })).toBeHidden();
  await expect(
    page.getByRole('heading', { name: 'По вашему запросу ничего не нашлось' }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Сбросить фильтры', exact: true }).click();
  await expect(search).toHaveValue('');
  await expect(page.getByRole('combobox', { name: 'Уровень сложности' })).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Все', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(cards.getByRole('link')).toHaveCount(6);
});

test('список сложности управляется клавиатурой и закрывается без изменения выбора', async ({
  page,
}) => {
  await page.goto('/catalog');
  const field = page.getByRole('combobox', { name: 'Уровень сложности' });
  const menu = page.getByRole('listbox', { name: 'Уровни сложности' });
  await field.focus();
  await field.press('Enter');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('option', { name: 'Все уровни', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await field.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(field).toHaveValue('Начальный');
  await expect(menu).toBeHidden();
  await field.press('Enter');
  await expect(menu).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(field).toBeFocused();
  await expect(field).toHaveValue('Начальный');
  await field.click();
  await expect(menu).toBeVisible();
  await page.getByRole('heading', { level: 1 }).click();
  await expect(menu).toBeHidden();
  await expect(field).toHaveValue('Начальный');
});

test('публичный кейс сохраняет адрес рабочего пространства через вход в демо', async ({ page }) => {
  await page.goto('/cases/customer-retention');
  await expect(
    page.getByRole('heading', { level: 1, name: 'Почему пользователи уходят?' }),
  ).toBeVisible();
  await page.getByRole('link', { name: /Начать кейс/ }).click();
  await expect(page).toHaveURL(/\/login\?returnUrl=%2Fcases%2Fcustomer-retention%2Fwork$/);
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/cases\/customer-retention\/work$/);
  await expect(page.getByRole('textbox', { name: 'Редактор: solution.md' })).toHaveText(
    /Основная метрика/,
  );
});

test('решение и шаги сохраняются, отправка фиксирует снимок и оставляет работу в ожидании', async ({
  page,
}) => {
  await register(page);
  await page.goto('/cases/customer-retention/work');
  const draft = page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true });
  const submit = page.getByRole('button', { name: 'Отправить на проверку', exact: true });
  await submit.click();
  await expect(page.getByRole('alert')).toContainText('Добавьте решение хотя бы в один файл');
  await draft.click();
  await draft.pressSequentially(solution);
  await expect(draft).toHaveText(solution);
  await page.getByRole('checkbox').nth(0).check();
  await page.reload();
  await expect(draft).toHaveText(solution);
  await expect(page.getByRole('checkbox').nth(0)).toBeChecked();
  await expect(page.getByRole('checkbox').nth(1)).not.toBeChecked();
  await submit.click();
  const confirmation = page.getByRole('dialog', { name: 'Отправить решение?' });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: 'Отмена', exact: true }).click();
  await expect(draft).toHaveAttribute('aria-readonly', 'false');
  await submit.click();
  await confirmation.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Решение на проверке', exact: true }),
  ).toBeVisible();
  await expect(draft).toHaveText(solution);
  await expect(draft).toHaveAttribute('aria-readonly', 'true');
  await expect(submit).toHaveCount(0);
  await expect(page.getByTestId('credits-balance')).toHaveText(/Кредиты\s*·\s*0$/);
  await page.reload();
  await expect(draft).toHaveText(solution);
  await expect(draft).toHaveAttribute('aria-readonly', 'true');
  await expect(page.getByRole('checkbox').nth(0)).toBeDisabled();
  await navigate(page, 'В работе');
  await expect(
    page.getByRole('link', { name: 'На проверке: Почему пользователи уходят?', exact: true }),
  ).toBeVisible();
  await navigate(page, 'Ревью');
  await expect(page.getByRole('region', { name: 'Работы на ревью' }).getByRole('link')).toHaveCount(
    3,
  );
});

test('прогресс изолирован между аккаунтами и возвращается после повторного входа', async ({
  page,
}) => {
  await register(page, 'first.user');
  await page.goto('/cases/customer-retention/work');
  await page.getByRole('textbox', { name: 'Редактор: solution.md' }).fill(solution);
  await page.getByRole('checkbox').nth(0).check();
  await logout(page);
  await register(page, 'second.user', 'Другой участник');
  await navigate(page, 'В работе');
  await expect(page.getByRole('heading', { name: 'Нет начатых кейсов' })).toBeVisible();
  await navigate(page, 'Ревью');
  await expect(page.getByRole('region', { name: 'Работы на ревью' }).getByRole('link')).toHaveCount(
    3,
  );
  await page.goto('/cases/customer-retention/work');
  await expect(page.getByRole('textbox', { name: 'Редактор: solution.md' })).toHaveText('');
  await expect(page.getByRole('checkbox').nth(0)).not.toBeChecked();
  await logout(page);
  await login(page, 'first.user');
  await navigate(page, 'В работе');
  await page
    .getByRole('link', { name: 'Продолжить: Почему пользователи уходят?', exact: true })
    .click();
  await expect(page.getByRole('textbox', { name: 'Редактор: solution.md' })).toHaveText(solution);
  await expect(page.getByRole('checkbox').nth(0)).toBeChecked();
});

test('демо содержит начатые и завершённые кейсы и отклоняет внешний адрес возврата', async ({
  page,
}) => {
  await page.goto('/login?returnUrl=https%3A%2F%2Fexample.com');
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await navigate(page, 'В работе');
  await expect(
    page.getByRole('region', { name: 'Кейсы', exact: true }).getByRole('link'),
  ).toHaveCount(2);
  await navigate(page, 'Каталог кейсов');
  const history = page.getByRole('region', { name: 'Кейсы', exact: true });
  await expect(history.getByRole('link')).toHaveCount(6);
  await history
    .getByRole('link', { name: 'Смотреть решение: Понятная подписка', exact: true })
    .click();
  await expect(page.getByText('Кейс завершён', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true }),
  ).toHaveAttribute('aria-readonly', 'true');
  await expect(page.getByRole('checkbox').nth(0)).toBeDisabled();
});
