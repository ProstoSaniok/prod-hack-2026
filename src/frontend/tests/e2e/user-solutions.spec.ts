import { expect, Page, test } from '@playwright/test';
import { readFile, mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, basename } from 'node:path';
import type { SolutionProject } from '../../src/app/cases/solutions/solution.models';

const storageKey = 'prodguild.solutions.v1';
const workUrl = '/cases/customer-retention/work';
const editor = (page: Page, path = 'solution.md') =>
  page.getByRole('textbox', { name: `Редактор: ${path}`, exact: true });

async function demo(page: Page): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto(workUrl);
  await expect(editor(page)).toBeVisible();
}

async function addFile(page: Page, path: string): Promise<void> {
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить файл', exact: true }).click();
  const field = page.getByRole('textbox', { name: 'Путь нового файла', exact: true });
  await field.click();
  await field.pressSequentially(path);
  await page.getByRole('button', { name: 'Создать файл', exact: true }).click();
  await expect(editor(page, path)).toBeVisible();
}

async function projects(page: Page): Promise<SolutionProject[]> {
  return page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? '[]'), storageKey);
}

async function submit(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Отправить на проверку', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Отправить решение?' })
    .getByRole('button', { name: 'Подтвердить отправку', exact: true })
    .click();
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
}

test('перенос сохраняет исходные записи, чек-лист и завершённые работы без дубликатов', async ({
  page,
}) => {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Попробовать демо', exact: true }).click();
  const original = await page.evaluate(() => localStorage.getItem('lotty.mock.work.v1.demo'));
  await page.goto(workUrl);
  await expect(editor(page)).toContainText('Основная метрика');
  await expect(page.getByRole('checkbox').nth(0)).toBeChecked();
  const first = (await projects(page))[0];
  await page.reload();
  await expect(editor(page)).toContainText('Основная метрика');
  expect((await projects(page)).map(project => project.id)).toEqual([first.id]);
  expect(await page.evaluate(() => localStorage.getItem('lotty.mock.work.v1.demo'))).toBe(original);
  await page.goto('/cases/subscription/work');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  await page.getByRole('tab', { name: 'Результаты', exact: true }).click();
  await expect(
    page.getByText('Подготовленный пример обратной связи', { exact: true }),
  ).toBeVisible();
  await expect(page.getByText('84 / 100', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('lotty.mock.work.v1.demo'))).toBe(original);
});

test('дерево, создание, сохранение, скачивание и удаление вложенного файла работают', async ({
  page,
}) => {
  await demo(page);
  await editor(page).fill('Черновик основного решения');
  await addFile(page, 'src/main.ts');
  const code = 'const total: number = 42;';
  await editor(page, 'src/main.ts').click();
  await editor(page, 'src/main.ts').pressSequentially(code);
  await editor(page, 'src/main.ts').press('ControlOrMeta+s');
  await expect(page.getByText('Версия файла сохранена в браузере.', { exact: true })).toBeVisible();
  const stored = (await projects(page))[0];
  expect(stored.files.find(file => file.path === 'src/main.ts')?.savedContent).toBe(code);
  expect(stored.files.find(file => file.path === 'solution.md')?.content).toBe(
    'Черновик основного решения',
  );
  await page.getByRole('button', { name: 'Папка src', exact: true }).click();
  await expect(page.getByRole('button', { name: 'src/main.ts', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Папка src', exact: true }).click();
  await page.getByRole('button', { name: 'solution.md', exact: true }).click();
  await expect(editor(page)).toHaveText('Черновик основного решения');
  await page.getByRole('button', { name: 'src/main.ts', exact: true }).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать', exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('main.ts');
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  if (downloadPath) expect(await readFile(downloadPath, 'utf8')).toBe(code);
  await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Удалить файл?' })
    .getByRole('button', { name: 'Отмена', exact: true })
    .click();
  await expect(editor(page, 'src/main.ts')).toHaveText(code);
  await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  await page
    .getByRole('dialog', { name: 'Удалить файл?' })
    .getByRole('button', { name: 'Удалить файл', exact: true })
    .click();
  await expect(editor(page)).toHaveText('Черновик основного решения');
  await expect(page.getByRole('button', { name: 'Папка src', exact: true })).toHaveCount(0);
  await page.reload();
  await expect(editor(page)).toHaveText('Черновик основного решения');
  expect((await projects(page))[0].files.map(file => file.path)).toEqual(['solution.md']);
});

test('редактор поддерживает отмену, повтор, поиск и безопасное переключение документов', async ({
  page,
}) => {
  await demo(page);
  await addFile(page, 'src/check.py');
  const code = 'total = 42';
  await editor(page, 'src/check.py').fill(code);
  await editor(page, 'src/check.py').press('End');
  await editor(page, 'src/check.py').pressSequentially('0');
  await expect(editor(page, 'src/check.py')).toHaveText('total = 420');
  await editor(page, 'src/check.py').press('ControlOrMeta+z');
  await expect(editor(page, 'src/check.py')).not.toHaveText('total = 420');
  await editor(page, 'src/check.py').press('ControlOrMeta+Shift+z');
  await expect(editor(page, 'src/check.py')).toHaveText('total = 420');
  await editor(page, 'src/check.py').press('ControlOrMeta+f');
  await expect(page.getByRole('textbox', { name: 'Найти', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Найти', exact: true }).fill('420');
  const nextMatch = page.getByRole('button', { name: 'Далее', exact: true });
  await nextMatch.hover();
  await page.mouse.down();
  await expect(nextMatch).toHaveCSS('background-image', 'none');
  await page.mouse.up();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'solution.md', exact: true }).click();
  await editor(page).press('ControlOrMeta+z');
  await expect(editor(page)).toContainText('Основная метрика');
  await page.getByRole('button', { name: 'README.md', exact: true }).click();
  const material = editor(page, 'Материалы/README.md');
  await expect(material).toHaveAttribute('aria-readonly', 'true');
  await material.click();
  await material.pressSequentially('НЕ МЕНЯТЬ');
  await expect(material).not.toContainText('НЕ МЕНЯТЬ');
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
});

test('прокрутка во время последовательного ввода не переставляет буквы и сохраняет точный черновик', async ({
  page,
}) => {
  await demo(page);
  await addFile(page, 'notes.md');
  const draft = editor(page, 'notes.md');
  const text =
    'Выбираю удержание на тридцатый день основной метрикой. Проверяю гипотезу о сложности первого перевода с помощью эксперимента.';
  await draft.evaluate(element => {
    // Reproduce scroll arriving after a native edit but before MutationObserver/selectionchange.
    element.addEventListener('input', () => element.dispatchEvent(new Event('scroll')));
  });
  await draft.click();
  await draft.pressSequentially(text);
  await expect(draft).toHaveText(text);
  await page.reload();
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'notes.md', exact: true }).click();
  await expect(draft).toHaveText(text);
});

test('нативное выделение перед вводом заменяет текст, а не дописывает к старому черновику', async ({
  page,
}) => {
  await demo(page);
  const draft = editor(page);
  const replacement = 'Новый текст вместо прежнего решения';
  await draft.fill(replacement);
  await expect(draft).toHaveText(replacement);
  const immediate = 'Замена до события selectionchange';
  await draft.evaluate((element, text) => {
    element.focus();
    const selection = element.ownerDocument.getSelection();
    if (!selection) throw new Error('Native selection is unavailable');
    const range = element.ownerDocument.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(
      new InputEvent('beforeinput', {
        bubbles: true,
        cancelable: true,
        inputType: 'insertText',
        data: text,
      }),
    );
  }, immediate);
  await expect(draft).toHaveText(immediate);
  await page.reload();
  await expect(draft).toHaveText(immediate);
});

test('обычный ввод сохраняет автоскобки и оборачивание выделения CodeMirror', async ({ page }) => {
  await demo(page);
  await addFile(page, 'src/brackets.ts');
  const draft = editor(page, 'src/brackets.ts');
  await draft.click();
  await draft.pressSequentially('(');
  await expect(draft).toHaveText('()');
  await draft.pressSequentially('value');
  await expect(draft).toHaveText('(value)');
  await draft.pressSequentially(')');
  await expect(draft).toHaveText('(value)');
  await draft.press('ControlOrMeta+a');
  await draft.pressSequentially('[');
  await expect(draft).toHaveText('[(value)]');
  await page.reload();
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'src/brackets.ts', exact: true }).click();
  await expect(draft).toHaveText('[(value)]');
});

test('неверные пути и совпадение файла с папкой отклоняются без потери черновика', async ({
  page,
}) => {
  await demo(page);
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить файл', exact: true }).click();
  for (const path of [
    '../secret.ts',
    '/absolute.ts',
    '.git/config',
    'src//main.ts',
    'solution.md/child.ts',
    'solution.md',
  ]) {
    await page.getByRole('textbox', { name: 'Путь нового файла', exact: true }).fill(path);
    await page.getByRole('button', { name: 'Создать файл', exact: true }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(editor(page)).toContainText('Основная метрика');
  }
  expect((await projects(page))[0].files).toHaveLength(1);
});

test('конфликт двух вкладок не затирает введённый текст и позволяет получить актуальную версию', async ({
  page,
  context,
}) => {
  await demo(page);
  await editor(page).fill('Мой несохранённый текст');
  const second = await context.newPage();
  await second.goto(workUrl);
  await expect(editor(second)).toHaveText('Мой несохранённый текст');
  await editor(second).fill('Новая версия из второй вкладки');
  await second.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(
    second.getByText('Версия файла сохранена в браузере.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Файл изменён в другой вкладке');
  await expect(editor(page)).toHaveText('Мой несохранённый текст');
  await expect(page.locator('.editor-footer')).toContainText('Изменения только на экране');
  expect((await projects(page))[0].files[0].savedContent).toBe('Новая версия из второй вкладки');
  await page.getByRole('button', { name: 'Загрузить актуальную версию', exact: true }).click();
  await expect(editor(page)).toHaveText('Новая версия из второй вкладки');
  await second.close();
});

test('отправка сохраняет все файлы и замораживает снимок даже при попытке сохранить из другой вкладки', async ({
  page,
  context,
}) => {
  await demo(page);
  await editor(page).fill('Моё готовое решение');
  await addFile(page, 'src/query.sql');
  await editor(page, 'src/query.sql').fill('select 42;');
  await page.getByRole('button', { name: 'solution.md', exact: true }).click();
  const second = await context.newPage();
  await second.goto(workUrl);
  await expect(editor(second)).toHaveText('Моё готовое решение');
  await submit(page);
  const saved = (await projects(page))[0];
  expect(saved.status).toBe('submitted');
  expect(saved.snapshot?.files).toEqual([
    { path: 'solution.md', content: 'Моё готовое решение' },
    { path: 'src/query.sql', content: 'select 42;' },
  ]);
  await editor(second).fill('Попытка изменить после отправки');
  await second.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(second.getByRole('alert')).toContainText('только для чтения');
  expect((await projects(page))[0].snapshot).toEqual(saved.snapshot);
  await page.reload();
  await expect(editor(page)).toHaveText('Моё готовое решение');
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'src/query.sql', exact: true }).click();
  await expect(editor(page, 'src/query.sql')).toHaveText('select 42;');
  await expect(editor(page, 'src/query.sql')).toHaveAttribute('aria-readonly', 'true');
  await expect(
    page.getByRole('button', { name: 'Отправить на проверку', exact: true }),
  ).toHaveCount(0);
  await second.close();
});

test('повтор подтверждения из другой вкладки возвращает ту же отправку', async ({
  page,
  context,
}) => {
  await demo(page);
  const second = await context.newPage();
  await second.goto(workUrl);
  await expect(editor(second)).toContainText('Основная метрика');
  await second.getByRole('button', { name: 'Отправить на проверку', exact: true }).click();
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'README.md', exact: true }).click();
  await submit(page);
  await expect(editor(page)).toContainText('Основная метрика');
  const original = (await projects(page))[0].snapshot;
  await second
    .getByRole('dialog', { name: 'Отправить решение?' })
    .getByRole('button', { name: 'Подтвердить отправку', exact: true })
    .click();
  await expect(editor(second)).toHaveAttribute('aria-readonly', 'true');
  expect((await projects(page))[0].snapshot).toEqual(original);
  await second.close();
});

test('повреждение файлового хранилища показывает ограничение, сохраняя исходный текст кейса', async ({
  page,
}) => {
  await demo(page);
  await page.evaluate(key => localStorage.setItem(key, '{broken'), storageKey);
  await page.reload();
  await expect(editor(page)).toContainText('Основная метрика');
  await expect(page.locator('.storage-warning')).toContainText('повреждено');
});

test('дерево и редактор помещаются в экран, длинный код прокручивается внутри редактора', async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await demo(page);
  await addFile(page, 'src/main.ts');
  await editor(page, 'src/main.ts').fill(
    'const greeting: string = "Привет, мир!";\n\nexport function greet(name: string): string {\n  return `${greeting} ${name}`;\n}',
  );
  await page.screenshot({
    path: `/tmp/prodguild-solutions-${testInfo.project.name}.png`,
    fullPage: true,
  });
  await editor(page, 'src/main.ts').fill('const longLine = "' + 'x'.repeat(3000) + '";');
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      ),
    )
    .toBeLessThanOrEqual(1);
  expect(
    await page
      .locator('.cm-scroller')
      .evaluate(element => element.scrollWidth > element.clientWidth),
  ).toBe(true);
  const toggle = page.getByRole('button', { name: 'Свернуть панель', exact: true });
  if (await toggle.isVisible()) {
    await toggle.click();
    await expect(page.getByRole('navigation', { name: 'Файлы решения' })).toBeHidden();
    await page.getByRole('button', { name: 'Развернуть панель', exact: true }).click();
    await expect(page.getByRole('navigation', { name: 'Файлы решения' })).toBeVisible();
  }
  await editor(page, 'src/main.ts').focus();
  await page.keyboard.press('Tab');
  await expect(editor(page, 'src/main.ts')).not.toBeFocused();
  expect(errors).toEqual([]);
});

test('компактные вкладки поддерживают клавиатуру, а отправка находится в верхней панели', async ({
  page,
}) => {
  await demo(page);
  const task = page.getByRole('tab', { name: 'Задание', exact: true });
  await task.focus();
  await page.keyboard.press('ArrowRight');
  const files = page.getByRole('tab', { name: 'Файлы', exact: true });
  await expect(files).toBeFocused();
  await expect(files).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End');
  await expect(page.getByRole('tab', { name: 'Результаты', exact: true })).toBeFocused();
  const tabWidths = await page
    .locator('.panel-tabs [role=tab]')
    .evaluateAll(elements => elements.map(element => element.getBoundingClientRect()));
  expect(Math.abs(tabWidths[1].left - tabWidths[0].right)).toBeLessThanOrEqual(4);
  expect(Math.abs(tabWidths[2].left - tabWidths[1].right)).toBeLessThanOrEqual(4);
  const button = page.getByRole('button', { name: 'Отправить на проверку', exact: true });
  await expect(
    page
      .locator('.workspace-heading')
      .getByRole('button', { name: 'Отправить на проверку', exact: true }),
  ).toBeVisible();
  expect((await button.boundingBox())?.y).toBeLessThan(350);
  await expect(page.locator('.submission-bar')).toHaveCount(0);
});

test('загрузка нескольких файлов сохраняет текст, вложения и неизменяемый снимок', async ({
  page,
}) => {
  await demo(page);
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  const binary = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0, 1, 2, 3]);
  await page.getByLabel('Загрузка файлов', { exact: true }).setInputFiles([
    { name: 'main.py', mimeType: 'text/x-python', buffer: Buffer.from('print("hello")') },
    { name: 'diagram.png', mimeType: 'image/png', buffer: binary },
  ]);
  await expect(editor(page, 'main.py')).toHaveText('print("hello")');
  await page.getByRole('button', { name: 'diagram.png', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'diagram.png', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0);
  const downloading = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать вложение', exact: true }).click();
  const download = await downloading;
  const path = await download.path();
  if (!path) throw new Error('Attachment download is missing');
  expect(await readFile(path)).toEqual(binary);
  await page.reload();
  await expect(editor(page)).toBeVisible();
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByRole('button', { name: 'diagram.png', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'diagram.png', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Отправить на проверку', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить отправку', exact: true }).click();
  await expect(editor(page)).toHaveAttribute('aria-readonly', 'true');
  const snapshot = (await projects(page))[0].snapshot;
  expect(snapshot?.files.find(file => file.path === 'diagram.png')?.binary?.base64).toBe(
    binary.toString('base64'),
  );
});

test('неудачная загрузка не перезаписывает файлы и не сохраняет часть набора', async ({ page }) => {
  await demo(page);
  await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
  await page.getByLabel('Загрузка файлов', { exact: true }).setInputFiles([
    { name: 'new.txt', mimeType: 'text/plain', buffer: Buffer.from('new file') },
    { name: 'solution.md', mimeType: 'text/markdown', buffer: Buffer.from('overwrite') },
  ]);
  await expect(page.getByRole('alert')).toContainText('Путь уже занят');
  await expect(editor(page)).toContainText('Основная метрика');
  expect((await projects(page))[0].files).toHaveLength(1);
  await page.getByLabel('Загрузка файлов', { exact: true }).setInputFiles({
    name: 'large.bin',
    mimeType: 'application/octet-stream',
    buffer: Buffer.alloc(1_000_001),
  });
  await expect(page.getByRole('alert')).toContainText('слишком большой');
  expect((await projects(page))[0].files).toHaveLength(1);
});

test('меню клонирования переключает HTTPS и SSH и копирует явно обозначенный демоадрес', async ({
  page,
  context,
}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await demo(page);
  const code = page.getByRole('button', { name: 'Код', exact: true });
  await code.click();
  const dropdown = page.getByRole('dialog', { name: 'Клонировать репозиторий', exact: true });
  await expect(dropdown).toBeVisible();
  const address = dropdown.getByRole('textbox', { name: 'Адрес репозитория', exact: true });
  await expect(address).toHaveValue('https://gitea.prodguild.test/demo/customer-retention.git');
  await dropdown.getByRole('button', { name: 'SSH', exact: true }).click();
  await expect(address).toHaveValue('git@gitea.prodguild.test:demo/customer-retention.git');
  await dropdown
    .getByRole('button', { name: 'Скопировать адрес репозитория', exact: true })
    .click();
  await expect(dropdown.getByRole('status')).toHaveText('Ссылка скопирована');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    'git@gitea.prodguild.test:demo/customer-retention.git',
  );
  await expect(dropdown.getByText(/Демоадрес Gitea/)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dropdown).toHaveCount(0);
  await expect(code).toBeFocused();
});

test('вкладки документов сохраняют черновики и историю отдельно для каждого файла', async ({
  page,
}) => {
  await demo(page);
  await addFile(page, 'src/one.ts');
  await editor(page, 'src/one.ts').fill('const first = 1;');
  await addFile(page, 'src/two.ts');
  await editor(page, 'src/two.ts').fill('const second = 2;');
  const tabs = page.getByRole('navigation', { name: 'Открытые файлы', exact: true });
  await tabs.getByRole('button', { name: 'Открытый файл src/one.ts', exact: true }).click();
  await expect(editor(page, 'src/one.ts')).toHaveText('const first = 1;');
  await editor(page, 'src/one.ts').press('ControlOrMeta+z');
  await expect(editor(page, 'src/one.ts')).not.toContainText('const first = 1;');
  await tabs.getByRole('button', { name: 'Открытый файл src/two.ts', exact: true }).click();
  await expect(editor(page, 'src/two.ts')).toHaveText('const second = 2;');
  await tabs.getByRole('button', { name: 'Закрыть вкладку src/two.ts', exact: true }).click();
  await page.getByRole('button', { name: 'src/two.ts', exact: true }).click();
  await expect(editor(page, 'src/two.ts')).toHaveText('const second = 2;');
});

test('загрузка папки сохраняет иерархию директорий', async ({ page }) => {
  const folder = await mkdtemp(join(tmpdir(), 'prodguild-upload-'));
  try {
    await mkdir(join(folder, 'src', 'utils'), { recursive: true });
    await writeFile(join(folder, 'src', 'utils', 'answer.ts'), 'export const answer = 42;');
    await demo(page);
    await page.getByRole('tab', { name: 'Файлы', exact: true }).click();
    await page.getByLabel('Загрузка папки', { exact: true }).setInputFiles(folder);
    const path = `${basename(folder)}/src/utils/answer.ts`;
    await expect(editor(page, path)).toHaveText('export const answer = 42;');
    await expect(
      page.getByRole('button', { name: `Папка ${basename(folder)}/src/utils`, exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(editor(page)).toBeVisible();
    expect((await projects(page))[0].files.some(file => file.path === path)).toBe(true);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});
