import { expect, Locator, Page, test } from '@playwright/test';

async function horizontalGeometry(locator: Locator): Promise<{ x: number; width: number }> {
  return locator.evaluate(element => {
    const { x, width } = element.getBoundingClientRect();
    return { x, width };
  });
}

async function expectToggleSurface(toggle: Locator, panelBackground: string): Promise<void> {
  await expect(toggle).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(toggle).toHaveCSS('overflow', 'visible');
  await expect(toggle.locator('.toggle-surface')).toHaveCSS('fill', panelBackground);
  await expect(toggle.locator('.toggle-surface')).toHaveCSS('height', '52px');
  await expect(toggle.locator('.toggle-surface')).toHaveCSS('top', '-4px');
}

async function expectNoPageOverflow(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
}

async function navigateTo(page: Page, section: string): Promise<void> {
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  if (!(await navigation.isVisible())) {
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  }
  await navigation.getByRole('link', { name: section, exact: true }).click();
  await expect(page.getByRole('heading', { name: section, exact: true, level: 1 })).toBeVisible();
}

test('стрелка раскрывает полосу иконок, а наведение и фокус не меняют её состояние', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'На телефоне используется модальный drawer');
  await page.goto('/sidebar');
  const sidebar = page.locator('app-sidebar');
  const panel = sidebar.locator('.sidebar');
  const content = page.getByTestId('workspace-content');
  const search = page.getByRole('searchbox', { name: 'Найти проект' });
  const show = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  await expect(sidebar).toHaveAttribute('data-expanded', 'false');
  await expect.poll(async () => (await horizontalGeometry(sidebar)).width).toBe(88);
  await expect(panel).toHaveJSProperty('inert', false);
  await expect(panel).toBeVisible();
  await expect(panel).toHaveCSS('border-radius', '40px');
  const projects = panel.getByRole('link', { name: 'Проекты', exact: true });
  await expect(projects).toHaveAttribute('aria-current', 'page');
  await expect(projects).toHaveAttribute('title', 'Проекты');
  await expect(projects.locator('.item-label')).toHaveCSS('opacity', '0');
  await expect(sidebar.locator('.brand-name')).toHaveText('ProdGuild');
  await expect(sidebar.locator('.brand-name')).toHaveCSS('opacity', '0');
  await expect(sidebar.locator('.brand-logo')).toHaveCSS('width', '44px');
  await expect(show).toHaveAttribute(
    'aria-controls',
    await sidebar.locator('.sidebar-frame').evaluate(element => element.id),
  );
  await expect.poll(async () => (await horizontalGeometry(show)).x).toBe(74);
  await expect(show).toHaveCSS('border-radius', '12px');
  await expect(show).toHaveCSS('width', '44px');
  const initialContent = await horizontalGeometry(content);
  await show.hover();
  await show.focus();
  await expect(sidebar).toHaveAttribute('data-expanded', 'false');
  await page.keyboard.press('Tab');
  await expect
    .poll(() => panel.evaluate(element => element.contains(document.activeElement)))
    .toBe(true);
  await projects.focus();
  await expect(projects).toBeFocused();
  await expect(sidebar).toHaveAttribute('data-expanded', 'false');
  await show.focus();
  await page.keyboard.press('Enter');
  await expect(sidebar).toHaveAttribute('data-expanded', 'true');
  await expect(panel).toBeVisible();
  await expect(panel).toHaveJSProperty('inert', false);
  await expect.poll(async () => (await horizontalGeometry(sidebar)).width).toBe(280);
  await expect.poll(async () => (await horizontalGeometry(panel)).width).toBe(280);
  await expect.poll(async () => (await horizontalGeometry(content)).x - initialContent.x).toBe(192);
  await expect(projects.locator('.item-label')).toHaveCSS('opacity', '1');
  await expect(sidebar.locator('.brand-name')).toHaveCSS('opacity', '1');
  const hide = page.getByRole('button', { name: 'Скрыть боковую панель', exact: true });
  await expect(hide).toHaveAttribute('aria-expanded', 'true');
  await expect.poll(async () => (await horizontalGeometry(hide)).x).toBe(266);
  await page.getByRole('link', { name: 'Команда', exact: true }).focus();
  await search.focus();
  await expect(sidebar).toHaveAttribute('data-expanded', 'true');
  await hide.click();
  await expect(show).toBeFocused();
  await expect(panel).toBeVisible();
  await expect(panel).toHaveJSProperty('inert', false);
  await expect(projects).toBeVisible();
  await expect(projects.locator('.item-label')).toHaveCSS('opacity', '0');
  await expect.poll(() => horizontalGeometry(content)).toEqual(initialContent);
  await expectNoPageOverflow(page);
});

test('сворачивание панели сохраняет рабочие поля и освобождает место контенту', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'На телефоне панель не меняет ширину контента');
  await page.goto('/sidebar');
  const content = page.getByTestId('workspace-content');
  const search = page.getByRole('searchbox', { name: 'Найти проект' });
  await search.fill('кабинет');
  const initialContent = await horizontalGeometry(content);
  await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  await expect
    .poll(async () => initialContent.width - (await horizontalGeometry(content)).width)
    .toBe(192);
  await expect(search).toHaveValue('кабинет');
  await expect(
    page.getByRole('region', { name: 'Список проектов' }).getByRole('button'),
  ).toHaveCount(1);
  await page.getByRole('button', { name: 'Скрыть боковую панель', exact: true }).click();
  await expect.poll(() => horizontalGeometry(content)).toEqual(initialContent);
  await expect(search).toHaveValue('кабинет');
  await expectNoPageOverflow(page);
});

test('поиск, пустой результат и выбор проекта меняют реальные данные страницы', async ({
  page,
}) => {
  await page.goto('/sidebar');
  const list = page.getByRole('region', { name: 'Список проектов' });
  const details = page.getByRole('complementary', { name: 'Сведения о проекте' });
  const search = page.getByRole('searchbox', { name: 'Найти проект' });
  await expect(list.getByRole('button')).toHaveCount(5);
  await search.click();
  await expect(search).toBeFocused();
  await page.keyboard.type('кабинет');
  await expect(list.getByRole('button')).toHaveCount(1);
  await search.fill('  КАБИНЕТ  ');
  await expect(list.getByRole('button')).toHaveCount(1);
  const project = list.getByRole('button', { name: /Личный кабинет/ });
  await project.click();
  await expect(project).toHaveAttribute('aria-pressed', 'true');
  await expect(details.getByRole('heading', { name: 'Личный кабинет', exact: true })).toBeVisible();
  await expect(details).toContainText('Михаил Орлов');
  await search.fill('Несуществующий проект');
  await expect(list.getByRole('button')).toHaveCount(0);
  await expect(list).toContainText('Проекты не найдены');
  await search.fill('');
  await expect(list.getByRole('button')).toHaveCount(5);
  await expect(project).toHaveAttribute('aria-pressed', 'true');
});

test('создание проверяет название и добавляет проект без переполнения', async ({ page }) => {
  await page.goto('/sidebar');
  await page.getByRole('button', { name: 'Новый проект', exact: true }).click();
  const title = page.getByRole('textbox', { name: 'Название проекта', exact: true });
  await title.fill('   ');
  await page.getByRole('button', { name: 'Добавить', exact: true }).click();
  await expect(title).toHaveAttribute('aria-invalid', 'true');
  await expect(title).toHaveAccessibleDescription('Введите название проекта');
  const longName = 'Проект'.repeat(13);
  await title.fill(longName);
  await page
    .getByRole('textbox', { name: 'Описание', exact: true })
    .fill('Проверка адаптивной сетки');
  await page.getByRole('button', { name: 'Добавить', exact: true }).click();
  const list = page.getByRole('region', { name: 'Список проектов' });
  await expect(list.getByRole('button')).toHaveCount(6);
  await expect(list.getByRole('button', { name: new RegExp(longName) })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(
    page.getByRole('complementary', { name: 'Сведения о проекте' }).getByRole('heading', {
      name: longName,
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole('status')).toContainText(longName);
  await expectNoPageOverflow(page);
});

test('навигация, история и прямой URL показывают выбранный раздел', async ({ page }) => {
  await page.goto('/sidebar#projects');
  await navigateTo(page, 'Команда');
  await expect(page).toHaveURL(/#team$/);
  await expect(
    page.getByRole('region', { name: 'Участники команды' }).getByRole('listitem'),
  ).toHaveCount(3);
  await navigateTo(page, 'Настройки');
  await expect(page).toHaveURL(/#settings$/);
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Команда', exact: true, level: 1 })).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'Команда', exact: true, includeHidden: true }),
  ).toHaveAttribute('aria-current', 'page');
  await page.goForward();
  await expect(
    page.getByRole('heading', { name: 'Настройки', exact: true, level: 1 }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Название пространства' })).toHaveValue(
    'Продуктовая команда',
  );
});

test('настройки валидируют и меняют название пространства', async ({ page }) => {
  await page.goto('/sidebar#settings');
  const name = page.getByRole('textbox', { name: 'Название пространства' });
  const save = page.getByRole('button', { name: 'Сохранить', exact: true });
  await name.fill('   ');
  await save.click();
  await expect(name).toHaveAttribute('aria-invalid', 'true');
  await expect(name).toHaveAccessibleDescription('Введите название пространства');
  await name.fill('  Исследования клиентов  ');
  await save.click();
  await expect(page.locator('.workspace-header')).toContainText('Исследования клиентов');
  await expect(page.getByRole('status')).toContainText('Название изменено в этой вкладке');
  await navigateTo(page, 'Проекты');
  await expect(page.locator('.workspace-header')).toContainText('Исследования клиентов');
});

test('мобильное меню удерживает фокус и закрывается по Escape, фону и переходу', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Модальное меню используется до 620px');
  await page.goto('/sidebar');
  const toggle = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  const dialog = page.getByRole('dialog', { name: 'Навигация', exact: true });
  const content = page.getByTestId('workspace-content');
  await expect(dialog).toBeHidden();
  await expect(page.locator('app-sidebar')).toHaveCSS('width', '0px');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveCSS('width', '280px');
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  await expect(dialog.locator('.brand-name')).toHaveText('ProdGuild');
  await expect(dialog.locator('.brand-name')).toHaveCSS('opacity', '1');
  await expect(content).toHaveJSProperty('inert', true);
  await expect(page.locator('body')).toHaveCSS('overflow', 'hidden');
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let index = 0; index < 10; index++) {
      await page.keyboard.press(key);
      await expect
        .poll(() => dialog.evaluate(element => element.contains(document.activeElement)))
        .toBe(true);
    }
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(toggle).toBeFocused();
  await expect(content).toHaveJSProperty('inert', false);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  await toggle.click();
  const backdrop = page.getByRole('button', { name: 'Закрыть меню', exact: true });
  await backdrop.click({ position: { x: (await horizontalGeometry(backdrop)).width - 4, y: 4 } });
  await expect(dialog).toBeHidden();
  await expect(toggle).toBeFocused();
  await toggle.click();
  const closeArrow = dialog.getByRole('button', { name: 'Скрыть боковую панель', exact: true });
  await expect(closeArrow).toBeFocused();
  const panelBackground = await dialog
    .locator('.sidebar')
    .evaluate(element => getComputedStyle(element).backgroundColor);
  await expectToggleSurface(closeArrow, panelBackground);
  await expect(closeArrow).toHaveCSS('width', '44px');
  await expect(closeArrow).toHaveCSS('height', '44px');
  await expect
    .poll(async () => {
      const panel = await horizontalGeometry(dialog);
      const arrow = await horizontalGeometry(closeArrow);
      return arrow.x + arrow.width - panel.x - panel.width;
    })
    .toBeCloseTo(18);
  await closeArrow.hover();
  await expectToggleSurface(closeArrow, panelBackground);
  await page.mouse.down();
  try {
    await expect.poll(() => closeArrow.evaluate(element => element.matches(':active'))).toBe(true);
    await expectToggleSurface(closeArrow, panelBackground);
  } finally {
    await page.mouse.up();
  }
  await expect(dialog).toBeHidden();
  await expect(toggle).toBeFocused();
  await toggle.click();
  await dialog.getByRole('link', { name: 'Команда', exact: true }).click();
  await expect(dialog).toBeHidden();
  const heading = page.getByRole('heading', { name: 'Команда', level: 1, exact: true });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(content).toHaveJSProperty('inert', false);
  await expectNoPageOverflow(page);
});

test('смена адаптивного режима освобождает фон и сбрасывает открытый drawer', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Проверка перехода через мобильный breakpoint');
  await page.goto('/sidebar');
  const toggle = page.getByRole('button', { name: 'Показать боковую панель', exact: true });
  const content = page.getByTestId('workspace-content');
  await toggle.click();
  await expect(content).toHaveJSProperty('inert', true);
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toHaveCount(0);
  await expect(content).toHaveJSProperty('inert', false);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  await expect(toggle).toBeVisible();
  await page.getByRole('searchbox', { name: 'Найти проект' }).fill('кабинет');
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('dialog', { name: 'Навигация', exact: true })).toBeHidden();
  await expect(page.getByRole('searchbox', { name: 'Найти проект' })).toHaveValue('кабинет');
  await expectNoPageOverflow(page);
});

test('переход из drawer на другую страницу освобождает прокрутку и фокус', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile', 'Проверка уничтожения открытого drawer');
  await page.setViewportSize({ width: 375, height: 300 });
  await page.goto('/sidebar');
  await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  const library = page
    .getByRole('dialog', { name: 'Навигация', exact: true })
    .getByRole('link', { name: 'UI-kit', exact: true });
  await library.scrollIntoViewIfNeeded();
  await expect(library).toBeInViewport();
  await expectNoPageOverflow(page);
  await library.click();
  await expect(page).toHaveURL(/\/ui-kit$/);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  const email = page.getByRole('textbox', { name: 'Почта', exact: true });
  await email.fill('person@example.com');
  await expect(email).toBeFocused();
  await expect(email).toHaveValue('person@example.com');
});

test('страница рабочего пространства доступна из UI-kit', async ({ page }) => {
  await page.goto('/ui-kit');
  await page.getByRole('link', { name: 'Sidebar', exact: true }).click();
  await expect(page).toHaveURL(/\/sidebar$/);
  await expect(page.getByRole('heading', { name: 'Проекты', exact: true, level: 1 })).toBeVisible();
});

test('desktop motion preserves icon positions and the attached toggle at every frame', async ({
  page,
}, info) => {
  test.skip(info.project.name === 'mobile', 'Desktop width transition');
  await page.goto('/sidebar');
  await page.evaluate(() => document.fonts.ready);
  const sidebar = page.locator('app-sidebar');
  await expect(sidebar).toHaveCSS('transition-duration', '0s');
  const toggle = sidebar.locator('.edge-toggle');
  const panelBackground = await sidebar
    .locator('.sidebar')
    .evaluate(element => getComputedStyle(element).backgroundColor);
  await expectToggleSurface(toggle, panelBackground);
  await expect(toggle).toHaveCSS('color', 'rgb(63, 63, 63)');
  await expect(toggle).toHaveCSS('box-shadow', 'none');
  await expect(toggle).toHaveCSS('border-radius', '12px');
  for (const target of [280, 88]) {
    const samples = await sidebar.evaluate(async host => {
      const button = host.querySelector<HTMLButtonElement>('.edge-toggle');
      const frame = host.querySelector<HTMLElement>('.sidebar-frame');
      const icon = host.querySelector<SVGElement>('.nav-item svg');
      const label = host.querySelector<HTMLElement>('.item-label');
      if (!button || !frame || !icon || !label) throw new Error('Sidebar controls missing');
      const before = icon.getBoundingClientRect();
      button.click();
      const samples = [];
      const started = performance.now();
      do {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
        const panel = frame.getBoundingClientRect();
        const arrow = button.getBoundingClientRect();
        const image = icon.getBoundingClientRect();
        samples.push({
          width: panel.width,
          iconOffset: Math.abs(image.x - before.x) + Math.abs(image.y - before.y),
          edgeOffset: Math.abs(arrow.right - panel.right - 18),
          centerOffset: Math.abs(arrow.y + arrow.height / 2 - panel.y - panel.height / 2),
          labelHeight: label.getBoundingClientRect().height,
          arrowWidth: arrow.width,
          arrowHeight: arrow.height,
        });
      } while (performance.now() - started < 320);
      return samples;
    });
    expect(samples.some(sample => sample.width > 89 && sample.width < 279)).toBe(true);
    expect(samples.at(-1)?.width).toBe(target);
    for (const sample of samples) {
      expect(sample.iconOffset).toBeLessThanOrEqual(1);
      expect(sample.edgeOffset).toBeLessThanOrEqual(1);
      expect(sample.centerOffset).toBeLessThanOrEqual(1);
      expect(sample.labelHeight).toBe(24);
      expect(sample.arrowWidth).toBe(44);
      expect(sample.arrowHeight).toBe(44);
    }
  }
  const reversed = await sidebar.evaluate(async host => {
    const button = host.querySelector<HTMLButtonElement>('.edge-toggle');
    if (!button) throw new Error('Sidebar toggle missing');
    button.click();
    const samples = [];
    const started = performance.now();
    let phase = 0;
    do {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const elapsed = performance.now() - started;
      samples.push({ phase, width: host.getBoundingClientRect().width });
      if (phase === 0 && elapsed > 60) {
        button.click();
        phase = 1;
      } else if (phase === 1 && elapsed > 120) {
        button.click();
        phase = 2;
      }
    } while (performance.now() - started < 440);
    return samples;
  });
  expect(reversed.every(sample => sample.width >= 88 && sample.width <= 280)).toBe(true);
  expect(reversed.at(-1)?.width).toBe(280);
  expect(reversed.filter(sample => sample.phase === 1).at(-1)?.width).toBeLessThan(
    reversed.find(sample => sample.phase === 1)?.width ?? 0,
  );
  for (const target of [88, 280]) {
    await toggle.hover();
    await expectToggleSurface(toggle, panelBackground);
    await page.mouse.down();
    try {
      await expect.poll(() => toggle.evaluate(element => element.matches(':active'))).toBe(true);
      await expectToggleSurface(toggle, panelBackground);
    } finally {
      await page.mouse.up();
    }
    await expect.poll(async () => (await horizontalGeometry(sidebar)).width).toBe(target);
  }
  await expectNoPageOverflow(page);
});

test('mobile drawer reverses its slide without stale focus, inert state or hidden opening', async ({
  page,
}, info) => {
  test.skip(info.project.name !== 'mobile', 'Mobile drawer motion');
  await page.goto('/sidebar');
  const sidebar = page.locator('app-sidebar');
  const frame = sidebar.locator('.sidebar-frame');
  await expect(frame).toHaveCSS('visibility', 'hidden');
  await expect(frame).toHaveJSProperty('inert', true);
  const samples = await sidebar.evaluate(async host => {
    const opener = host.querySelector<HTMLButtonElement>('.mobile-opener');
    const close = host.querySelector<HTMLButtonElement>('.mobile-close');
    const frame = host.querySelector<HTMLElement>('.sidebar-frame');
    const backdrop = host.querySelector<HTMLElement>('.backdrop');
    const content = document.querySelector<HTMLElement>('[data-testid="workspace-content"]');
    if (!opener || !close || !frame || !backdrop || !content)
      throw new Error('Mobile controls missing');
    opener.focus();
    opener.click();
    const samples = [];
    let phase = 0;
    const started = performance.now();
    do {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const elapsed = performance.now() - started;
      samples.push({
        phase,
        x: frame.getBoundingClientRect().x,
        visibility: getComputedStyle(frame).visibility,
        pointerEvents: getComputedStyle(frame).pointerEvents,
        backdropOpacity: Number(getComputedStyle(backdrop).opacity),
        frameInert: frame.inert,
        backgroundInert: content.inert,
        focusedInside: frame.contains(document.activeElement),
        openerFocused: document.activeElement === opener,
        bodyLocked: document.body.style.overflow === 'hidden',
      });
      if (phase === 0 && elapsed > 80) {
        close.click();
        phase = 1;
      } else if (phase === 1 && elapsed > 160) {
        opener.click();
        phase = 2;
      }
    } while (performance.now() - started < 480);
    return { samples, retained: host.querySelector('.sidebar-frame') === frame };
  });
  expect(samples.retained).toBe(true);
  expect(
    samples.samples.some(sample => sample.phase === 0 && sample.x > -300 && sample.x < 7),
  ).toBe(true);
  expect(
    samples.samples.some(sample => sample.backdropOpacity > 0 && sample.backdropOpacity < 1),
  ).toBe(true);
  for (const sample of samples.samples) {
    expect(sample.visibility).toBe('visible');
    expect(sample.frameInert).toBe(sample.phase === 1);
    expect(sample.backgroundInert).toBe(sample.phase !== 1);
    expect(sample.bodyLocked).toBe(sample.phase !== 1);
    expect(sample.pointerEvents).toBe(sample.phase === 1 ? 'none' : 'auto');
    expect(sample.focusedInside).toBe(sample.phase !== 1);
    expect(sample.openerFocused).toBe(sample.phase === 1);
  }
  expect(samples.samples.at(-1)?.x).toBe(8);
  await page.keyboard.press('Escape');
  await expect(frame).toHaveJSProperty('inert', true);
  await expect(frame).toHaveCSS('visibility', 'hidden');
  await expect(page.getByTestId('workspace-content')).toHaveJSProperty('inert', false);
  await expect(sidebar.locator('.mobile-opener')).toBeFocused();
});

test('reduced motion has no width, slide or delayed-visibility transition', async ({
  page,
}, info) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/sidebar');
  const mobile = info.project.name === 'mobile';
  const samples = await page.locator('app-sidebar').evaluate(async (host, mobile) => {
    const frame = host.querySelector<HTMLElement>('.sidebar-frame');
    const opener = host.querySelector<HTMLButtonElement>('.edge-toggle');
    if (!frame || !opener) throw new Error('Sidebar missing');
    const samples = [];
    for (let action = 0; action < 2; action++) {
      const button =
        mobile && action === 1 ? host.querySelector<HTMLButtonElement>('.mobile-close') : opener;
      button?.click();
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      samples.push({
        width: host.getBoundingClientRect().width,
        x: frame.getBoundingClientRect().x,
        visibility: getComputedStyle(frame).visibility,
        animations: host.getAnimations().length + frame.getAnimations().length,
      });
    }
    return samples;
  }, mobile);
  expect(samples.every(sample => sample.animations === 0)).toBe(true);
  if (mobile) {
    expect(samples[0]?.x).toBe(8);
    expect(samples[1]?.visibility).toBe('hidden');
  } else {
    expect(samples.map(sample => sample.width)).toEqual([280, 88]);
  }
});

test('crossing the mobile breakpoint cancels motion and releases the dialog immediately', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/sidebar');
  const sidebar = page.locator('app-sidebar');
  await sidebar.locator('.mobile-opener').evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.getByTestId('workspace-content')).toHaveJSProperty('inert', true);
  await page.setViewportSize({ width: 768, height: 1024 });
  await expect(sidebar).toHaveCSS('width', '88px');
  await expect(page.getByTestId('workspace-content')).toHaveJSProperty('inert', false);
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  expect(await sidebar.evaluate(element => element.getAnimations().length)).toBe(0);
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(sidebar.locator('.sidebar-frame')).toHaveCSS('visibility', 'hidden');
  await expect(sidebar.locator('.sidebar-frame')).toHaveJSProperty('inert', true);
  expect(
    await sidebar.locator('.sidebar-frame').evaluate(element => element.getAnimations().length),
  ).toBe(0);
});
