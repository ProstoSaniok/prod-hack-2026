import { expect, test } from '@playwright/test';

// Requires the local seed and waiting submissions prepared with tools/seed-local-review-demo.py.
// The two accounts have confirmed SQL grades; each test completes one real assignment.
for (const label of ['Ревьюер−', 'Ревьюер+']) {
  test(`${label}: start → inspect files → send feedback without a manual save`, async ({
    page,
  }, info) => {
    test.setTimeout(90000);
    await page.goto('/login');
    await page.getByRole('button', { name: label, exact: true }).click();
    await expect(page).toHaveURL(/\/reviews$/);
    const claim = page.waitForResponse(
      response =>
        response.url().endsWith('/api/v1/me/review-assignments') &&
        response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Начать ревью', exact: true }).click();
    const claimed = await claim;
    expect([200, 201], 'Prepare waiting demo submissions before this integration test').toContain(
      claimed.status(),
    );
    const assignment = (await claimed.json()) as { id: string; kind: string };
    expect(assignment.kind).toBe('review');
    await expect(page).toHaveURL(new RegExp(`/reviews/${assignment.id}$`));
    await expect(page.getByRole('heading', { name: 'Задание', exact: true })).toBeVisible();
    const training = (await page.locator('h1').innerText()).startsWith('[Демо ревью');
    const score = training ? '60' : '85';
    await page.getByRole('button', { name: '2. Файлы решения', exact: true }).click();
    await page.getByRole('button', { name: 'Все файлы', exact: true }).click();
    await page
      .getByRole('navigation', { name: 'Файлы решения' })
      .getByRole('button')
      .first()
      .click();
    await expect(page.getByRole('textbox', { name: /^Редактор:/ })).not.toBeEmpty();
    await page.screenshot({ path: info.outputPath('review-files.png'), fullPage: true });
    await page
      .getByLabel('Комментарий', { exact: true })
      .fill('Решение проверено в локальном демонстрационном сценарии.');
    await page.getByRole('button', { name: 'Добавить комментарий', exact: true }).click();
    await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
    await page.getByLabel('Оценка от 0 до 100').fill(score);
    await page.getByLabel('Вердикт', { exact: true }).click();
    await page
      .getByRole('listbox')
      .getByRole('option', { name: training ? 'Нужны изменения' : 'Принято', exact: true })
      .click();
    await page
      .getByLabel('Сильные стороны', { exact: true })
      .fill('Запрос оформлен читаемо, используются подходящие имена таблиц и полей.');
    await page
      .getByLabel('Проблемы', { exact: true })
      .fill(
        training
          ? 'Условия отбора не сохраняют все требуемые строки. Сверьте WHERE и соединение таблиц с заданием.'
          : 'Критичных ошибок в демонстрационном решении не обнаружено.',
      );
    await page
      .getByLabel('Следующие шаги', { exact: true })
      .fill('Добавьте описание проверки результата и граничных случаев.');
    const saved = page.waitForResponse(
      response =>
        response.url().endsWith(`/review-assignments/${assignment.id}/draft`) &&
        response.request().method() === 'PUT',
    );
    await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
    expect((await saved).status()).toBe(200);
    await expect(
      page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }),
    ).toBeVisible();
    await page.screenshot({ path: info.outputPath('review-confirmation.png'), fullPage: true });
    const completed = page.waitForResponse(
      response =>
        response.url().endsWith(`/review-assignments/${assignment.id}/reviews`) &&
        response.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
    expect((await completed).status()).toBe(201);
    await expect(page).toHaveURL(/\/review-results\/[^/]+$/);
    await expect(page.getByRole('region', { name: 'Опубликованное ревью' })).toContainText(
      `${score} / 100`,
    );
    await page.reload();
    await expect(page.getByRole('region', { name: 'Опубликованное ревью' })).toContainText(
      `${score} / 100`,
    );
    await page.goto('/profile');
    await expect(page.getByText('Награда за ревью', { exact: true }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  });
}
