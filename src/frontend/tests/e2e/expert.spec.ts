import { expect, Page, test } from '@playwright/test';

// HTTP fixtures exercise frontend role transitions only, not server authorization.
async function backend(page: Page): Promise<void> {
  await page.route('**/auth/sign_in/email', async route => {
    const email = route.request().postDataJSON().email;
    await route.fulfill({ json: { access_token: email, token_type: 'bearer' } });
  });
  await page.route('**/auth/me', route => {
    const email = route.request().headers()['authorization']?.replace('Bearer ', '') ?? '';
    return route.fulfill({
      json: {
        id: email,
        role: 'user',
        capabilities: email.startsWith('organizer') ? ['organizer'] : [],
      },
    });
  });
  await page.route('**/auth/sign_out', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/me/review-mode') {
      const email = route.request().headers()['authorization']?.replace('Bearer ', '') ?? '';
      return route.fulfill({
        json: {
          enabled: false,
          status: 'disabled',
          can_review: email.startsWith('mentor.') || email.startsWith('review-'),
        },
      });
    }
    return route.fulfill({
      json: path.endsWith('/me/wallet')
        ? { settled: 10, reserved: 0, available: 10, unit: 'credit' }
        : [],
    });
  });
}
async function role(page: Page, label = 'Эксперт'): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: label, exact: true }).click();
  const destination =
    label === 'Эксперт' ? '/expert' : label.startsWith('Ревьюер') ? '/reviews' : '/catalog';
  await expect(page).toHaveURL(new RegExp(`${destination}$`));
}
async function finish(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toBeVisible();
  await page.getByRole('button', { name: 'Подтвердить публикацию', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toBeVisible();
}
async function quality(page: Page, score = '85'): Promise<void> {
  await page.getByLabel('Качество ревью · 0–100', { exact: true }).fill(score);
  await page.getByLabel('Сильные стороны · обязательно').fill('Хорошая структура комментариев');
  await page.getByLabel('Проблемы · обязательно').fill('Пропущена обработка пробелов');
  await page.getByLabel('Рекомендации · обязательно').fill('Проверяйте граничные случаи');
}
async function overflow(page: Page): Promise<void> {
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
  ).toBeLessThanOrEqual(1);
}

test.beforeEach(async ({ page }) => backend(page));

test('очереди, редактор, комментарии и первичная проверка без балла', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await role(page);
  await expect(
    page.getByRole('region', { name: 'Демонстрационные работы' }).getByRole('link'),
  ).toHaveCount(2);
  await page.getByRole('button', { name: 'Проверены ревьюэром', exact: true }).click();
  await expect(page.getByText('Ревьюэр: Ревьюер−', { exact: true })).toBeVisible();
  await expect(page.getByText('Ревьюэр: Ревьюер+', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Без ревью', exact: true }).click();
  await page.getByRole('link', { name: 'Открыть работу: Поиск по каталогу', exact: true }).click();
  const editor = page.getByRole('textbox', { name: 'Редактор: src/search.ts', exact: true });
  await expect(editor).toHaveAttribute('aria-readonly', 'true');
  await expect(page.getByLabel('Качество ревью · 0–100', { exact: true })).toHaveCount(0);
  await page.getByLabel('Комментарий', { exact: true }).fill('Нужно нормализовать запрос');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Комментарии эксперта' })).toContainText(
    'Нужно нормализовать запрос',
  );
  await page.getByRole('button', { name: 'Ответить', exact: true }).click();
  await page.getByLabel('Комментарий', { exact: true }).fill('Добавьте тест с пробелами');
  await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
  await page.getByRole('button', { name: 'Отметить решённым', exact: true }).click();
  await page.getByLabel('Фидбэк участнику · обязательно').click();
  await page
    .getByLabel('Фидбэк участнику · обязательно')
    .pressSequentially('Проверьте пробелы в запросе');
  await page.getByLabel('Вердикт решения', { exact: true }).selectOption('changes_requested');
  await page.reload();
  await expect(page.getByLabel('Фидбэк участнику · обязательно')).toHaveValue(
    'Проверьте пробелы в запросе',
  );
  await expect(page.getByRole('region', { name: 'Комментарии эксперта' })).toContainText(
    'Добавьте тест с пробелами',
  );
  await overflow(page);
  await page.screenshot({ path: `/tmp/prodguild-expert-${info.project.name}.png`, fullPage: true });
  await finish(page);
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toContainText(
    'Нужны изменения',
  );
  await expect(page.getByRole('button', { name: 'Завершить проверку', exact: true })).toHaveCount(
    0,
  );
  await page.reload();
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toContainText(
    'Проверьте пробелы',
  );
  await page.goto('/expert');
  await page.getByRole('button', { name: 'Завершённые', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Демонстрационные работы' }).getByRole('link'),
  ).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('эксперт заменяет итог; участник и нужный ревьюэр видят свои результаты', async ({ page }) => {
  await role(page);
  await page.goto('/expert/reviewed-catalog-search');
  await expect(page.getByRole('region', { name: 'Исходное ревью' })).toContainText(
    'Решение принято',
  );
  await page
    .getByLabel('Фидбэк участнику · обязательно')
    .fill('Нужна обработка пробелов и категорий');
  await page.getByLabel('Вердикт решения', { exact: true }).selectOption('changes_requested');
  await quality(page);
  await finish(page);
  await expect(page.getByRole('region', { name: 'Исходное ревью' })).toContainText(
    'Решение принято',
  );
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toContainText(
    'Нужны изменения',
  );
  await role(page, 'Исполнитель');
  await page.goto('/demo-results');
  await expect(
    page.getByRole('region', { name: 'Демонстрационные работы' }).getByRole('link'),
  ).toHaveCount(4);
  await page.goto('/demo-results/reviewed-catalog-search');
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toContainText(
    'Нужна обработка пробелов',
  );
  await expect(page.getByRole('region', { name: 'Оценка качества ревью' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Завершить проверку', exact: true })).toHaveCount(
    0,
  );
  await role(page, 'Ревьюер−');
  await page.goto('/reviews');
  await expect(page.getByRole('region', { name: 'Обратная связь эксперта' })).toContainText(
    '85 / 100',
  );
  await page.goto('/reviews/catalog-search');
  await expect(page.getByRole('region', { name: 'Обратная связь эксперта' })).toContainText(
    'Проверяйте граничные случаи',
  );
  await page.reload();
  await expect(page.getByRole('region', { name: 'Обратная связь эксперта' })).toContainText(
    'Пропущена обработка пробелов',
  );
  await role(page, 'Ревьюер+');
  await page.goto('/reviews');
  await expect(page.getByRole('region', { name: 'Обратная связь эксперта' })).toHaveCount(0);
  await page.goto('/demo-results/reviewed-catalog-search');
  await expect(
    page.getByText('Работа не найдена или недоступна этому тестовому аккаунту.'),
  ).toBeVisible();
});

test('валидация пустого заключения, целого балла и подтверждения от 70', async ({ page }) => {
  await role(page);
  await page.goto('/expert/reviewed-catalog-search');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await expect(page.locator('#expert-validation')).toContainText('Добавьте итоговый фидбэк');
  await page.getByLabel('Фидбэк участнику · обязательно').fill('Исправьте поиск');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await expect(page.locator('#expert-validation')).toContainText('целым баллом');
  await quality(page, '69');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await expect(page.locator('#expert-validation')).toContainText('не менее 70');
  await page.getByLabel('Вердикт по ревью').selectOption('needs_revision');
  await finish(page);
  await expect(page.getByRole('region', { name: 'Оценка качества ревью' })).toContainText(
    'На доработку',
  );
});

test('гость, другая роль, неизвестная работа и сохранение прямого адреса', async ({ page }) => {
  await page.goto('/expert/new-catalog-search');
  await expect(page).toHaveURL(/\/login\?returnUrl=/);
  await page.getByLabel('Email', { exact: true }).fill('mentor.local@example.com');
  await page.getByLabel('Пароль', { exact: true }).fill('Local-demo-2026!');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/expert\/new-catalog-search$/);
  await page.goto('/expert/missing');
  await expect(page.getByRole('heading', { name: 'Работа не найдена', exact: true })).toBeVisible();
  await role(page, 'Исполнитель');
  await page.goto('/expert/new-catalog-search');
  await expect(page).toHaveURL(/\/catalog$/);
  await role(page, 'Ревьюер−');
  await page.goto('/demo-results');
  await expect(
    page.getByRole('region', { name: 'Демонстрационные работы' }).getByRole('link'),
  ).toHaveCount(1);
});

test('черновик не публикуется другим ролям; подтверждение отменяется при правке', async ({
  page,
}) => {
  await role(page);
  await page.goto('/expert/new-catalog-search');
  await page.getByLabel('Фидбэк участнику · обязательно').fill('Личный черновик эксперта');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await page.getByRole('button', { name: 'Продолжить редактирование', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Подтвердить публикацию' })).toHaveCount(0);
  await role(page, 'Исполнитель');
  await page.goto('/demo-results/new-catalog-search');
  await expect(page.getByText('Ожидает проверки экспертом', { exact: true })).toBeVisible();
  await expect(page.getByText('Личный черновик эксперта', { exact: true })).toHaveCount(0);
  await role(page);
  await page.goto('/expert/new-catalog-search');
  await expect(page.getByLabel('Фидбэк участнику · обязательно')).toHaveValue(
    'Личный черновик эксперта',
  );
});

test('повреждённое хранилище и ошибки записи не теряют состояние сессии', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('prodguild.expert-results.v1.reviewed-catalog-search', '{broken');
    localStorage.setItem('prodguild.expert-drafts.v1.new-catalog-search', '{broken');
    const native = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string): void {
      if (key.startsWith('prodguild.expert-')) throw new Error('Storage full');
      native.call(this, key, value);
    };
  });
  await role(page);
  await expect(page.getByRole('status').filter({ hasText: 'Повреждённые записи' })).toBeVisible();
  await page.goto('/expert/new-catalog-search');
  await expect(
    page.getByRole('status').filter({ hasText: 'Черновик не восстановлен' }),
  ).toBeVisible();
  await page.getByLabel('Фидбэк участнику · обязательно').fill('Результат в памяти');
  await expect(page.getByRole('status').filter({ hasText: 'Хранилище недоступно' })).toBeVisible();
  await finish(page);
  const logoutMenu = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await logoutMenu.isVisible()) await logoutMenu.click();
  await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
  await page.getByRole('button', { name: 'Исполнитель', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  // SPA navigation keeps the documented in-memory fallback alive without reloading.
  const opener = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await opener.isVisible()) await opener.click();
  await page.getByRole('link', { name: 'Результаты демо-работ', exact: true }).click();
  await page
    .getByRole('link', { name: 'Открыть работу: Поиск по каталогу', exact: true })
    .last()
    .click();
  await expect(page.getByRole('region', { name: 'Итог эксперта', exact: true })).toContainText(
    'Результат в памяти',
  );
});

test('баллы 0, 70, 100 и дробные значения; клавиатурное подтверждение', async ({ page }) => {
  await role(page);
  await page.goto('/expert/reviewed-retention-analysis');
  await page.getByLabel('Фидбэк участнику · обязательно').fill('Добавьте граничные тесты');
  await quality(page, '70.5');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
  await expect(page.locator('#expert-validation')).toContainText('целым баллом');
  for (const value of ['-1', '101', '0']) {
    await page.getByLabel('Качество ревью · 0–100', { exact: true }).fill(value);
    await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toHaveCount(0);
  }
  for (const value of ['70', '100']) {
    await page.getByLabel('Качество ревью · 0–100', { exact: true }).fill(value);
    await page.getByRole('button', { name: 'Завершить проверку', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toBeVisible();
    await page.getByRole('button', { name: 'Продолжить редактирование', exact: true }).click();
  }
  await page.getByLabel('Качество ревью · 0–100', { exact: true }).fill('0');
  await page.getByLabel('Вердикт по ревью').selectOption('needs_revision');
  await page.getByRole('button', { name: 'Завершить проверку', exact: true }).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Подтвердить публикацию', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('region', { name: 'Оценка качества ревью' })).toContainText(
    '0 / 100',
  );
});

test('серверные маршруты каталога и редактор проекта сохраняют свой контракт', async ({ page }) => {
  const project = {
    id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    case_id: 'server-case',
    case_version: 1,
    status: 'active',
    head_commit_sha: 'head',
  };
  const study = {
    id: 'server-case',
    title: 'Серверный кейс',
    description: 'Задание с сервера',
    expected_result: 'Рабочий запрос',
    difficulty: 'review_minus',
    content_version: 1,
    competencies: [],
  };
  await page.route('**/api/v1/cases?*', route => route.fulfill({ json: [study] }));
  await page.route('**/api/v1/me/projects', route => route.fulfill({ json: project }));
  await page.route('**/api/v1/projects/' + project.id, route => route.fulfill({ json: project }));
  await page.route('**/api/v1/projects/*/files', route =>
    route.fulfill({ json: [{ path: 'solution.sql', sha: 'file-sha', commit_sha: 'head' }] }),
  );
  let content = 'SELECT 1;';
  let saved = false;
  await page.route('**/api/v1/projects/*/files/content?*', async route => {
    if (route.request().method() === 'PUT') {
      content = route.request().postDataJSON().content;
      saved = true;
    }
    await route.fulfill({
      json: { path: 'solution.sql', sha: 'file-sha', commit_sha: 'head', content },
    });
  });
  await role(page, 'Исполнитель');
  await expect(page.getByRole('heading', { name: 'Серверный кейс', exact: true })).toBeVisible();
  await page.goto('/cases/server-case/work');
  await expect(page).toHaveURL(new RegExp('/cases/server-case/work$'));
  const editor = page.getByRole('textbox', { name: 'Редактор: solution.sql', exact: true });
  await expect(editor).toHaveText('SELECT 1;');
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.pressSequentially('SELECT 42;');
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect.poll(() => saved).toBe(true);
  expect(content).toBe('SELECT 42;');
  await page.reload();
  await expect(editor).toHaveText('SELECT 42;');
});

test('отдельная сессия админки сохраняет кабинет эксперта и единое меню', async ({ page }) => {
  await role(page);
  await page.goto('/admin');
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto('/expert');
  await expect(page.getByRole('heading', { name: 'Кабинет эксперта', exact: true })).toBeVisible();
  await page.goto('/profile');
  await expect(page.getByRole('heading', { name: 'Профиль и история', exact: true })).toBeVisible();
  await expect(page.getByText('mentor.local@example.com', { exact: true })).toBeVisible();
  await role(page, 'Исполнитель');
  const opener = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  if (await opener.isVisible()) await opener.click();
  await expect(page.getByRole('link', { name: 'Ревью', exact: true })).toHaveCount(0);
  await role(page, 'Ревьюер+');
  if (await opener.isVisible()) await opener.click();
  await expect(page.getByRole('link', { name: 'Ревью', exact: true })).toHaveCount(1);
  await expect(page.getByRole('link', { name: 'Завершённые', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Ревью', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Ревью', exact: true })).toBeVisible();
});
