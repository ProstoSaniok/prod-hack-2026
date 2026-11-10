import { expect, Locator, Page, test } from '@playwright/test';

async function saveFile(page: Page, scope: Page | Locator = page): Promise<void> {
  if (scope === page) {
    await page.getByRole('textbox', { name: /^Редактор:/ }).press('ControlOrMeta+S');
    await expect(page.getByText('Сохранено', { exact: true })).toBeVisible({ timeout: 100000 });
    return;
  }
  await scope.getByRole('textbox', { name: /^Редактор:/ }).press('ControlOrMeta+S');
  await expect(scope.locator('.save-status')).toHaveText('Сохранено', { timeout: 100000 });
}

test('publish → edit → submit → inspect a real immutable solution', async ({ page }, testInfo) => {
  test.setTimeout(240000);
  const title = `SQL: локальное демо ${Date.now()}`;
  const email = `journey-${crypto.randomUUID()}@example.com`;
  const password = 'Integration-test-123';
  await page.goto('/login');
  await page.getByRole('link', { name: 'Админ', exact: true }).click();
  await expect(page).toHaveURL(/\/admin$/, { timeout: 15000 });
  await page.getByRole('link', { name: 'Создать кейс', exact: true }).click();
  await page.getByLabel('Название кейса', { exact: true }).fill(title);
  await page
    .getByLabel('Описание', { exact: true })
    .fill('Исправьте SQL-запрос и сохраните результат в файле.');
  await page.getByLabel('Ожидаемый результат', { exact: true }).fill('Корректный SQL-запрос.');
  await page.getByRole('button', { name: 'Компетенции кейса', exact: true }).click();
  await page.getByRole('checkbox', { name: 'SQL', exact: true }).check();
  await page.getByRole('button', { name: 'Готово', exact: true }).click();
  await expect(page.locator('.save-status').first()).toHaveText('Сохранено', { timeout: 15000 });
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/[^/]+\/edit$/);
  await page.getByRole('link', { name: 'Материалы', exact: true }).click();
  const adminUrl = page.url();
  const materials = page.getByRole('region', { name: 'Материалы кейса', exact: true });
  await materials.getByRole('button', { name: 'Добавить файл', exact: true }).first().click();
  await materials.getByLabel('Путь нового файла').fill('task.sql');
  const created = page.waitForResponse(
    r => r.url().includes('/files?') && r.request().method() === 'POST',
  );
  await materials.getByRole('button', { name: 'Создать файл', exact: true }).click();
  expect((await created).status()).toBe(201);
  const task = materials.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(task).toBeEditable();
  await task.click();
  await task.pressSequentially('SELECT 1 AS answer;');
  await expect(task).toHaveText('SELECT 1 AS answer;');
  await saveFile(page, materials);
  await page.reload();
  await expect(task).toHaveText('SELECT 1 AS answer;');
  await page.getByRole('link', { name: 'Эталонное решение', exact: true }).click();
  const initialized = page.waitForResponse(
    r => r.url().includes('/reference-solution?') && r.request().method() === 'PUT',
  );
  await page.getByRole('button', { name: 'Создать эталон', exact: true }).click();
  expect((await initialized).status()).toBe(201);
  const reference = page.getByRole('region', { name: 'Эталонное решение', exact: true });
  await expect(
    reference.getByRole('textbox', { name: 'Редактор: task.sql', exact: true }),
  ).toBeVisible();
  await reference.getByRole('button', { name: 'Добавить файл', exact: true }).first().click();
  await reference.getByLabel('Путь нового файла').fill('private-answer.sql');
  await reference.getByRole('button', { name: 'Создать файл', exact: true }).click();
  const answer = reference.getByRole('textbox', {
    name: 'Редактор: private-answer.sql',
    exact: true,
  });
  await expect(answer).toBeEditable();
  await answer.click();
  await answer.pressSequentially('SELECT 42 AS answer;');
  await expect(answer).toHaveText('SELECT 42 AS answer;');
  await saveFile(page, reference);
  await page.reload();
  await reference.getByRole('button', { name: 'private-answer.sql', exact: true }).click();
  await expect(answer).toHaveText('SELECT 42 AS answer;');
  await page.getByRole('button', { name: 'Эталон готов', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Опубликовать кейс', exact: true })).toBeEnabled();
  const published = page.waitForResponse(
    r => /\/admin\/cases\/[^/?]+$/.test(r.url()) && r.request().method() === 'PATCH',
  );
  await page.getByRole('button', { name: 'Опубликовать кейс', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить публикацию', exact: true }).click();
  expect((await published).status()).toBe(200);
  await expect(page.getByText('Опубликован', { exact: true })).toBeVisible();
  await page.goto('/register');
  await page.getByLabel('Ваше имя', { exact: true }).fill('Новый участник');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByLabel('Повторите пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Зарегистрироваться', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page
    .getByRole('link')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    .click();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('private-answer.sql', { exact: true })).toHaveCount(0);
  const started = page.waitForResponse(
    r => r.url().endsWith('/api/v1/me/projects') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Решать кейс', exact: true }).click();
  expect((await started).status()).toBe(201);
  await expect(page).toHaveURL(/\/projects\/[^/]+$/);
  const projectUrl = page.url();
  await page.goto(adminUrl);
  await expect(
    page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true }),
  ).not.toBeEditable();
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Добавить файл', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Описание', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Сохранить описание', exact: true })).toHaveCount(
    0,
  );
  await page.goto(projectUrl);
  const projectEditor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(projectEditor).toContainText('SELECT 1 AS answer;');
  await page.getByRole('button', { name: 'Отправить решение', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText(
    'Решение не отличается от исходных материалов',
  );
  await page.getByRole('button', { name: 'Отмена', exact: true }).click();
  await projectEditor.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.insertText('SELECT 2 AS answer;');
  await saveFile(page);
  await page.reload();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true }),
  ).toContainText('SELECT 2 AS answer;');
  await page.getByRole('button', { name: 'Добавить файл', exact: true }).first().click();
  await page.getByLabel('Путь нового файла').fill('notes.md');
  await page.getByRole('button', { name: 'Создать файл', exact: true }).click();
  await expect(
    page.getByRole('textbox', { name: 'Редактор: notes.md', exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить удаление файла', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Редактор: notes.md', exact: true })).toHaveCount(
    0,
  );
  await page.goto('/my-cases');
  await expect(page.getByRole('link', { name: new RegExp(title) })).toBeVisible();
  await page.goto(projectUrl);
  await expect(projectEditor).toContainText('SELECT 2 AS answer;');
  await projectEditor.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.insertText(
    Array.from({ length: 130 }, (_, index) => `SELECT ${index + 2} AS answer;`).join('\n'),
  );
  const submitButton = page.getByRole('button', { name: 'Отправить решение', exact: true });
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(projectEditor).toContainText('SELECT 2 AS answer;');
  await expect(submitButton).toBeEnabled();
  await submitButton.click();
  await page
    .getByLabel('Комментарий к решению (необязательно)')
    .fill('Проверка полного пути из браузера');
  let originalKey = '';
  let originalBody = '';
  let interrupted = false;
  await page.route('**/api/v1/projects/*/submissions', async route => {
    if (!interrupted) {
      interrupted = true;
      originalKey = route.request().headers()['idempotency-key'];
      originalBody = route.request().postData() ?? '';
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ code: 'DEPENDENCY_UNAVAILABLE', message: 'Test interruption' }),
      });
    } else {
      expect(route.request().headers()['idempotency-key']).toBe(originalKey);
      expect(route.request().postData()).toBe(originalBody);
      await route.continue();
    }
  });
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText(
    'Сервис временно недоступен. Повторите операцию с теми же данными.',
  );
  await expect(page.getByLabel('Комментарий к решению (необязательно)')).not.toBeEditable();
  const submitted = page.waitForResponse(
    r => /\/projects\/[^/]+\/submissions$/.test(r.url()) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  const result = await submitted;
  expect(result.status()).toBe(201);
  const solution = (await result.json()) as { id: string; commit_sha: string };
  await expect(page).toHaveURL(/\/submissions\/[^/]+$/);
  const submissionUrl = page.url();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await page
    .getByRole('navigation', { name: 'Файлы решения' })
    .getByRole('button', { name: 'task.sql', exact: true })
    .click();
  const snapshotEditor = page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true });
  await expect(snapshotEditor).toContainText('SELECT 2 AS answer;');
  const snapshotScroller = page.locator('app-snapshot-viewer .cm-scroller');
  await snapshotScroller.scrollIntoViewIfNeeded();
  await expect
    .poll(async () => {
      await snapshotScroller.evaluate(element => element.scrollTo(0, element.scrollHeight));
      return snapshotEditor.textContent();
    })
    .toContain('SELECT 131 AS answer;');
  await page.getByRole('button', { name: 'Исходные файлы', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Исходные файлы', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page
    .getByRole('navigation', { name: 'Файлы решения' })
    .getByRole('button', { name: 'task.sql', exact: true })
    .click();
  await expect(page.getByRole('textbox', { name: 'Редактор: task.sql', exact: true })).toHaveText(
    'SELECT 1 AS answer;',
  );
  await page.locator('app-snapshot-viewer summary').click();
  await page.getByRole('list', { name: 'Изменённые файлы' }).getByRole('button').click();
  await expect(page.getByLabel('Различия по строкам')).toContainText('SELECT 1 AS answer;');
  await page.getByRole('button', { name: 'Следующие строки', exact: true }).click();
  await expect(page.getByLabel('Различия по строкам')).toContainText('SELECT 131 AS answer;');
  await page.getByRole('button', { name: 'Предыдущие строки', exact: true }).click();
  await expect(page.getByLabel('Различия по строкам')).toContainText('SELECT 1 AS answer;');
  await expect(page.getByRole('button', { name: 'Скачать снимок JSON', exact: true })).toHaveCount(
    0,
  );
  await expect(page.getByText(solution.commit_sha, { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Повысить приоритет', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить приоритет', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Отменить приоритет', exact: true })).toBeVisible();
  await expect(page.getByTestId('credits-balance')).toContainText('5');
  await page.getByRole('button', { name: 'Отменить приоритет', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Повысить приоритет', exact: true })).toBeVisible();
  await expect(page.getByTestId('credits-balance')).toContainText('10');
  await page.goto('/my-cases/completed');
  await page
    .getByRole('link')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })
    .click();
  await expect(page).toHaveURL(submissionUrl);
  await page.goto(projectUrl);
  await expect(
    page.getByRole('link', { name: 'Открыть отправленное решение', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Отправить решение', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  const unauthorized = await page.request.get(`/api/v1/submissions/${solution.id}`);
  expect(unauthorized.status()).toBe(401);
  await page.goto('/login');
  await page.getByRole('button', { name: 'Ревьюер+', exact: true }).click();
  await expect(page).toHaveURL(/\/reviews$/);
  await page.goto(submissionUrl);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Файлы', exact: true })).toHaveCount(0);
  await page.goto('/reviews');
  const claimed = page.waitForResponse(
    r => r.url().endsWith('/api/v1/me/review-assignments') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Начать ревью', exact: true }).click();
  const assignmentResponse = await claimed;
  expect(assignmentResponse.status()).toBe(201);
  expect((await assignmentResponse.json()).submission_id).toBe(solution.id);
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByLabel('Оценка от 0 до 100').fill('85');
  await page.getByLabel('Вердикт', { exact: true }).click();
  await page.getByRole('option', { name: 'Принято', exact: true }).click();
  await page
    .getByLabel('Сильные стороны', { exact: true })
    .fill('Запрос возвращает требуемый результат.');
  await page.getByLabel('Проблемы', { exact: true }).fill('Критичных ошибок нет.');
  await page
    .getByLabel('Следующие шаги', { exact: true })
    .fill('Добавьте проверки граничных случаев.');
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  await expect(page).toHaveURL(/\/review-results\//);
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto(submissionUrl);
  await expect(page.getByRole('region', { name: 'Опубликованное ревью' })).toContainText(
    '85 / 100',
  );
  await expect(page.getByTestId('credits-balance')).toContainText('13');
  await page.goto('/profile#wallet');
  await expect(page.getByRole('list', { name: 'Ваши навыки' })).toContainText('SQL');
  await expect(page.getByText('Награда за кейс', { exact: true })).toBeVisible();
  await testInfo.attach('demo-project', {
    body: JSON.stringify({ title, projectUrl }),
    contentType: 'application/json',
  });
});
