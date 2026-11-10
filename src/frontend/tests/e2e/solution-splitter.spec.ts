import { expect, Page, test } from '@playwright/test';

const separator = (page: Page) => page.getByRole('separator', { name: 'Ширина панели информации' });
const editor = (page: Page) =>
  page.getByRole('textbox', { name: 'Редактор: solution.md', exact: true });

async function open(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto('/cases/customer-retention/work');
  await expect(editor(page)).toBeVisible();
}

async function value(page: Page): Promise<number> {
  return Number(await separator(page).getAttribute('aria-valuenow'));
}

test('splitter clamps mouse and keyboard resizing and resets to 30 percent', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  const handle = separator(page);
  await expect(handle).toBeVisible();
  await expect(handle).toHaveAttribute('aria-orientation', 'vertical');
  await expect(handle).toHaveAttribute('aria-controls', 'workspace-information workspace-editor');
  await handle.focus();
  await handle.press('Home');
  expect(await value(page)).toBe(280);
  await handle.press('ArrowLeft');
  expect(await value(page)).toBe(280);
  await handle.press('ArrowRight');
  expect(await value(page)).toBe(296);
  await handle.press('End');
  expect(await value(page)).toBe(Number(await handle.getAttribute('aria-valuemax')));
  await handle.press('Enter');
  const initial = await value(page);
  const bounds = await handle.boundingBox();
  if (!bounds) throw new Error('Splitter is not visible');
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + 100);
  await page.mouse.down();
  await page.mouse.move(0, bounds.y + 100, { steps: 5 });
  expect(await value(page)).toBe(280);
  await page.mouse.move(1440, bounds.y + 100, { steps: 5 });
  expect(await value(page)).toBe(Number(await handle.getAttribute('aria-valuemax')));
  await page.mouse.up();
  await expect(page.locator('app-split-pane')).not.toHaveClass(/dragging/);
  await handle.dblclick();
  expect(await value(page)).toBe(initial);
});

test('sidebar and narrow-container changes preserve mounted editor, content and Undo', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1000, height: 900 });
  await open(page);
  await editor(page).fill('Сохранённый текст');
  await editor(page).press('End');
  await editor(page).pressSequentially(' — правка');
  const originalEditor = await editor(page).elementHandle();
  await expect(separator(page)).toBeHidden();
  await page.getByRole('button', { name: 'Скрыть боковую панель', exact: true }).click();
  await expect(separator(page)).toBeVisible();
  await separator(page).focus();
  await separator(page).press('End');
  await expect(editor(page)).toHaveText('Сохранённый текст — правка');
  await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await expect(separator(page)).toBeHidden();
  expect(await originalEditor?.evaluate(element => element.isConnected)).toBe(true);
  await editor(page).focus();
  await editor(page).press('ControlOrMeta+z');
  await expect(editor(page)).not.toHaveText('Сохранённый текст — правка');
  await editor(page).press('ControlOrMeta+Shift+z');
  await expect(editor(page)).toHaveText('Сохранённый текст — правка');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole('button', { name: 'Свернуть панель', exact: true }).click();
  await expect(page.locator('#workspace-panel')).toBeHidden();
  await expect(editor(page)).toHaveText('Сохранённый текст — правка');
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(separator(page)).toBeVisible();
  await expect(page.locator('#workspace-panel')).toBeVisible();
  expect(await originalEditor?.evaluate(element => element.isConnected)).toBe(true);
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
});

test('touch dragging changes width and releases capture after cancellation', async ({
  page,
  context,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  const handle = separator(page);
  const bounds = await handle.boundingBox();
  if (!bounds) throw new Error('Splitter is not visible');
  const initial = await value(page);
  const client = await context.newCDPSession(page);
  const point = { x: bounds.x + bounds.width / 2, y: bounds.y + 100 };
  await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] });
  await client.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ ...point, x: point.x + 100 }],
  });
  await expect.poll(() => value(page)).toBeGreaterThan(initial + 50);
  await client.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] });
  await expect(page.locator('app-split-pane')).not.toHaveClass(/dragging/);
  const afterCancel = await value(page);
  await page.mouse.move(point.x + 200, point.y);
  expect(await value(page)).toBe(afterCancel);
  await client.detach();
});
