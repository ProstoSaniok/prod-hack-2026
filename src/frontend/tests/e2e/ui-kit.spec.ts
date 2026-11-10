import { expect, test } from '@playwright/test';

test('ошибка почты соответствует текущему значению поля', async ({ page }) => {
  await page.goto('/ui-kit');
  const email = page.getByRole('textbox', { name: 'Почта', exact: true });

  await expect(email).toHaveAttribute('aria-invalid', 'true');
  await expect(email).toHaveAccessibleDescription('Проверьте адрес почты');
  await email.fill('person@example.com');
  await expect(email).toHaveAttribute('aria-invalid', 'false');
  await expect(page.locator('#field-error')).toBeEmpty();

  await email.fill('');
  await expect(email).toHaveAttribute('aria-invalid', 'true');
  await expect(email).toHaveAccessibleDescription('Проверьте адрес почты');
});

test('аккордеон раскрывается с клавиатуры и сообщает своё состояние', async ({ page }) => {
  await page.goto('/ui-kit');
  const heading = page.getByRole('button', { name: 'Как использовать состояния?', exact: true });
  const panel = page.getByRole('region', { name: 'Как использовать состояния?', exact: true });

  await expect(heading).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();
  await heading.focus();
  await page.keyboard.press('Enter');
  await expect(heading).toHaveAttribute('aria-expanded', 'true');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Показывайте состояния загрузки');

  await page.keyboard.press('Escape');
  await expect(heading).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();
  await expect(heading).toBeFocused();

  await page.keyboard.press('Space');
  await expect(panel).toBeVisible();
});
