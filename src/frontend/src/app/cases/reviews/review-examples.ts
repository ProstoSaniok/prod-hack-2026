import { ReviewExample } from './review.models';

/** Fictional submissions for the local review demonstration, never user projects. */
export const reviewExamples: readonly ReviewExample[] = [
  {
    id: 'catalog-search',
    title: 'Поиск по каталогу',
    author: 'Алексей Морозов',
    initials: 'АМ',
    category: 'Разработка',
    language: 'TypeScript',
    summary: 'Поиск продуктов по названию и категории с сортировкой по популярности.',
    task: 'Помочь пользователю быстро найти продукт. Поиск должен учитывать регистр, пробелы и пустой запрос.',
    approach:
      'Вынес фильтрацию в отдельную функцию. Добавил примеры данных и тесты для основных сценариев.',
    files: [
      {
        path: 'src/search.ts',
        content: `export interface Product {
  id: string;
  name: string;
  category: string;
  popularity: number;
}

export function searchProducts(products: Product[], query: string): Product[] {
  const normalized = query.toLowerCase();

  return products
    .filter(product => product.name.toLowerCase().includes(normalized))
    .sort((left, right) => right.popularity - left.popularity);
}
`,
      },
      {
        path: 'tests/search.test.ts',
        content: `import { expect, test } from 'vitest';
import { searchProducts } from '../src/search';

const products = [
  { id: '1', name: 'Карта для путешествий', category: 'Карты', popularity: 15 },
  { id: '2', name: 'Накопительный счёт', category: 'Счета', popularity: 8 },
];

test('поиск не зависит от регистра', () => {
  expect(searchProducts(products, 'КАРТА')).toHaveLength(1);
});
`,
      },
      {
        path: 'README.md',
        content: `# Поиск по каталогу

Решение: Алексей Морозов. Вымышленный пример для демо-ревью.

Поиск работает по названию продукта. Результаты отсортированы по популярности.

## Что проверить
- Пробелы вокруг запроса.
- Поиск по категории продукта.
- Пустой запрос и пустой каталог.
`,
      },
    ],
    threads: [
      {
        id: 'search-normalize',
        author: 'Мария Волкова',
        text: 'Добавь trim(): сейчас запрос « карта » не найдёт продукт. Хорошо бы закрепить этот сценарий тестом.',
        path: 'src/search.ts',
        line: 9,
        resolved: false,
        replies: [],
      },
    ],
  },
  {
    id: 'retention-analysis',
    title: 'Почему пользователи уходят?',
    author: 'Дарья Соколова',
    initials: 'ДС',
    category: 'Аналитика',
    language: 'Python',
    summary: 'Когортный анализ удержания и гипотеза об улучшении первого перевода.',
    task: 'Посчитать удержание клиентов на тридцатый день и предложить измеримый эксперимент.',
    approach:
      'Сгруппировала пользователей по дате регистрации и сравнила активность в конце первого месяца.',
    files: [
      {
        path: 'analysis/retention.py',
        content: `from datetime import timedelta


def retention_d30(users, events):
    active_users = set()
    for user in users:
        target = user.created_at.date() + timedelta(days=30)
        if any(event.user_id == user.id and event.date == target for event in events):
            active_users.add(user.id)

    return len(active_users) / len(users)
`,
      },
      {
        path: 'analysis/cohorts.sql',
        content: `SELECT
  DATE_TRUNC('week', created_at) AS cohort,
  COUNT(*) AS users_count
FROM users
GROUP BY cohort
ORDER BY cohort;
`,
      },
      {
        path: 'README.md',
        content: `# Удержание на тридцатый день

Решение: Дарья Соколова. Вымышленный пример для демо-ревью.

Гипотеза: упрощение первого перевода повысит удержание.
Основная метрика: D30 retention. Защитная метрика: число ошибочных переводов.

Следующий шаг: добавить проверку пустой когорты и определить окно активности.
`,
      },
    ],
    threads: [
      {
        id: 'retention-empty',
        author: 'Илья Кузнецов',
        text: 'Что вернёт функция для пустой когорты? Сейчас здесь будет деление на ноль.',
        path: 'analysis/retention.py',
        line: 11,
        resolved: false,
        replies: [],
      },
    ],
  },
  {
    id: 'subscription-flow',
    title: 'Понятная подписка',
    author: 'Никита Орлов',
    initials: 'НО',
    category: 'Разработка',
    language: 'JavaScript',
    summary: 'Расчёт стоимости подписки с пробным периодом и промокодом.',
    task: 'Показать пользователю итоговую цену до подключения подписки и объяснить условия пробного периода.',
    approach:
      'Разделил расчёт цены и описание тарифа. Предусмотрел отдельный сценарий пробного периода.',
    files: [
      {
        path: 'src/pricing.js',
        content: `export function calculatePrice(plan, discount = 0) {
  if (plan.trial) return 0;

  const price = plan.monthlyPrice * (1 - discount / 100);
  return Math.round(price);
}

export function describePlan(plan) {
  return plan.trial ? 'Первый месяц бесплатно' : 'Оплата каждый месяц';
}
`,
      },
      {
        path: 'data/plan.json',
        content: `{
  "name": "Плюс",
  "monthlyPrice": 299,
  "trial": false,
  "currency": "RUB"
}
`,
      },
      {
        path: 'README.md',
        content: `# Понятная подписка

Решение: Никита Орлов. Вымышленный пример для демо-ревью.

Цена рассчитывается с учётом скидки. Пробный период обрабатывается отдельно.

Для проверки: скидка больше 100%, отрицательная скидка и точность округления.
`,
      },
    ],
    threads: [
      {
        id: 'pricing-discount',
        author: 'Мария Волкова',
        text: 'Ограничь диапазон скидки: при значении 150 цена становится отрицательной.',
        path: 'src/pricing.js',
        line: 4,
        resolved: false,
        replies: [],
      },
    ],
  },
];
