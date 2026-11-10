import { expect, Page, Route, test } from '@playwright/test';
import { emptyAttachments } from './fixtures/attachments';

const userId = 'workflow-user';
const submissionId = 'solution-one';
const applicationId = 'application-one';
const wallet = { settled: 10, reserved: 0, available: 10, unit: 'credit' };
const policy = {
  version: 'prodguild-development-v1',
  progression_version: 'p02-v1',
  state: 'configured',
  formula: 'score * weight',
  success_score: 70,
  quality_score: 70,
  probation_required_reviews: 3,
  display_cap: 100,
  priority_cost: 5,
  grade_gates: ['performer', 'review_minus', 'review_plus', 'expert'].map((grade, index) => ({
    grade,
    minimum_value: index * 25,
    successful_cases: index ? 2 : 0,
    confirmed_good_reviews: index > 1 ? 3 : 0,
    previous_probation_required: index > 1,
    independent_readiness_required: index === 3,
  })),
};
const skill = {
  competency_id: 'skill-one',
  name: 'SQL',
  grade: 'review_plus',
  value: 80,
  contribution_sum: 80,
  version: 12,
  grant: 'confirmed',
  review_allowed: true,
  expert_confirmed: false,
  probation: null,
  expert_application: { can_apply: true, pending_application_id: null },
};
const study = {
  id: 'case-one',
  title: 'Поиск по каталогу',
  description: 'Найдите ошибку.',
  expected_result: 'Рабочий поиск.',
  difficulty: 'review_plus',
  content_version: 1,
  competencies: [],
};
const application = {
  id: applicationId,
  user_id: 'applicant',
  competency_id: skill.competency_id,
  competency_name: 'SQL',
  status: 'pending',
  version: 1,
  created_at: '2026-10-17T10:00:00Z',
  current_user_competency_version: 12,
  can_assess: true,
  assessments: [],
  evidence_summary: {
    value: '80',
    successful_cases: [{ case_id: 'case-one', title: study.title, score: '90' }],
    probations: [
      {
        id: 'probation-one',
        grade: 'review_plus',
        confirmed_reviews: 3,
        completed_at: '2026-10-16T10:00:00Z',
      },
    ],
    reviews: [
      {
        review_id: 'portfolio-one',
        case_title: study.title,
        published_at: '2026-10-15T10:00:00Z',
        quality_score: '95',
      },
    ],
  },
};

type Handler = (route: Route, url: URL) => Promise<boolean>;
async function setup(page: Page, handler?: Handler, expert = false): Promise<void> {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('prodguild.api.session.v1'))
      sessionStorage.setItem(
        'prodguild.api.session.v1',
        JSON.stringify({ token: 'workflow-token', email: 'user@example.com', name: 'Участник' }),
      );
  });
  await page.route('**/auth/me', route =>
    route.fulfill({ json: { id: userId, role: 'user', capabilities: ['participant'] } }),
  );
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    if (handler && (await handler(route, url))) return;
    const path = url.pathname;
    if (path.endsWith('attachments')) return route.fulfill({ json: emptyAttachments(1) });
    if (path === '/api/v1/me/review-mode')
      return route.fulfill({
        json: {
          enabled: false,
          status: 'disabled',
          can_review: true,
          can_assess_readiness: expert,
          can_quality_review: expert,
        },
      });
    if (path === '/api/v1/me/wallet') return route.fulfill({ json: wallet });
    if (path === '/api/v1/me/profile')
      return route.fulfill({
        json: {
          id: userId,
          display_name: 'Участник',
          grade: 'review_plus',
          competencies: [skill],
          wallet,
          karma: 3,
          prodits: 10,
        },
      });
    if (path === '/api/v1/development/policy') return route.fulfill({ json: policy });
    if (path === '/api/v1/me/history') return route.fulfill({ json: [] });
    if (path === '/api/v1/me/priority-requests') return route.fulfill({ json: [] });
    if (path === '/api/v1/me/wallet/transactions') return route.fulfill({ json: [] });
    if (path === '/api/v1/me/reviewer-applications' || path === '/api/v1/reviewer-applications')
      return route.fulfill({
        json: { items: [application], next_offset: null, offset: 0, limit: 20 },
      });
    if (path === `/api/v1/reviewer-applications/${applicationId}`)
      return route.fulfill({ json: application });
    if (path === `/api/v1/reviewer-applications/${applicationId}/evidence/reviews/portfolio-one`)
      return route.fulfill({
        json: {
          review_id: 'portfolio-one',
          revision_id: 'revision-one',
          case: study,
          published_at: '2026-10-15T10:00:00Z',
          score: '90',
          verdict: 'approved',
          feedback: {
            strengths: 'Точные рекомендации',
            problems: 'Слабые места найдены',
            next_steps: 'Добавить проверки',
          },
          comments: [],
          quality: { score: '95', confirmed_at: '2026-10-16T10:00:00Z' },
        },
      });
    if (path === '/api/v1/cases/case-one') return route.fulfill({ json: study });
    if (path === '/api/v1/cases') return route.fulfill({ json: [study] });
    if (path.endsWith('/feedback')) return route.fulfill({ json: { reviews: [] } });
    if (path.endsWith('/progression')) return route.fulfill({ json: { contributions: [] } });
    if (path.endsWith('/files') || path.endsWith('/diff'))
      return route.fulfill({ json: { entries: [], next_offset: null } });
    return route.fulfill({ json: [] });
  });
}
async function noOverflow(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({ path: test.info().outputPath('workflow.png'), fullPage: true });
}

for (const failure of ['unavailable', 'malformed success'] as const) {
  test(`priority reserves once and retries the exact request after ${failure}`, async ({
    page,
  }) => {
    let status: 'reserved' | 'cancelled' | null = null;
    let purchases = 0;
    const keys: string[] = [];
    const request = () => ({
      id: 'priority-one',
      submission_id: submissionId,
      cost: 5,
      created_at: '2026-10-17T10:00:00Z',
      status,
    });
    await setup(page, async (route, url) => {
      if (url.pathname === `/api/v1/submissions/${submissionId}`) {
        await route.fulfill({
          json: {
            id: submissionId,
            project_id: 'project-one',
            case_id: 'case-one',
            case_version: 1,
            status: 'submitted',
            submitted_at: '2026-10-17T10:00:00Z',
            commit_sha: 'head',
            base_commit_sha: 'base',
            priority: {
              cost: 5,
              can_purchase: status !== 'reserved',
              can_cancel: status === 'reserved',
              current_request: status ? request() : null,
            },
          },
        });
        return true;
      }
      if (url.pathname.endsWith('/priority-requests') && route.request().method() === 'POST') {
        keys.push(route.request().headers()['idempotency-key']);
        purchases++;
        status = 'reserved';
        await route.fulfill(
          purchases === 1
            ? failure === 'unavailable'
              ? { status: 503, json: { code: 'DEPENDENCY_UNAVAILABLE' } }
              : { status: 200, contentType: 'application/json', body: '{"id":' }
            : { json: request() },
        );
        return true;
      }
      if (
        url.pathname === '/api/v1/priority-requests/priority-one' &&
        route.request().method() === 'DELETE'
      ) {
        status = 'cancelled';
        await route.fulfill({ status: 204 });
        return true;
      }
      return false;
    });
    await page.goto(`/submissions/${submissionId}`);
    await page.getByRole('button', { name: 'Повысить приоритет', exact: true }).click();
    expect(purchases).toBe(0);
    await page.getByRole('button', { name: 'Подтвердить приоритет', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Повторить запрос приоритета' })).toBeVisible();
    await page.reload();
    await page.getByRole('button', { name: 'Повторить запрос приоритета' }).click();
    await expect(
      page.getByRole('button', { name: 'Отменить приоритет', exact: true }),
    ).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[0]).toBe(keys[1]);
    await page.getByRole('button', { name: 'Отменить приоритет', exact: true }).click();
    await expect(page.getByText('Приоритет отменён', { exact: false })).toBeVisible();
    await noOverflow(page);
  });
}

test('profile activity paginates and private feedback events do not link to reviewer routes', async ({
  page,
}) => {
  const offsets: string[] = [];
  await setup(page, async (route, url) => {
    if (url.pathname === '/api/v1/me/history') {
      const offset = url.searchParams.get('offset') ?? '0';
      offsets.push(offset);
      await route.fulfill({
        json:
          offset === '0'
            ? Array.from({ length: 20 }, (_, index) => ({
                id: `feedback:${index}`,
                type: 'feedback_published',
                resource_id: `review-${index}`,
                description: `Получен отзыв ${index}`,
                created_at: '2026-10-17T10:00:00Z',
              }))
            : [
                {
                  id: 'started:one',
                  type: 'case_started',
                  resource_id: 'project-one',
                  description: 'Начат кейс',
                  created_at: '2026-10-16T10:00:00Z',
                },
              ],
      });
      return true;
    }
    return false;
  });
  await page.goto('/profile');
  await page.getByRole('button', { name: 'Активность', exact: true }).click();
  const activity = page.getByRole('list', { name: 'Активность', exact: true });
  await expect(activity.getByRole('listitem').first()).toContainText('Получен отзыв 0');
  await expect(activity.getByRole('link')).toHaveCount(0);
  await page.getByRole('button', { name: 'Следующие события' }).click();
  await expect(activity.getByRole('link', { name: 'Начат кейс' })).toHaveAttribute(
    'href',
    '/projects/project-one',
  );
  expect(offsets).toEqual(['0', '20']);
  await page.getByRole('button', { name: 'Приоритет проверок', exact: true }).click();
  await expect(page.getByText('История приоритета пока пуста.')).toBeVisible();
  await page.getByRole('button', { name: 'Заявки', exact: true }).click();
  await expect(page.getByRole('link', { name: /SQL.*Ожидает эксперта/ })).toHaveAttribute(
    'href',
    `/reviewer-applications/${applicationId}`,
  );
  await noOverflow(page);
});

test('eligible skill creates an application with the current aggregate version', async ({
  page,
}) => {
  let body: unknown;
  await setup(page, async (route, url) => {
    if (url.pathname === '/api/v1/reviewer-applications' && route.request().method() === 'POST') {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: { id: applicationId } });
      return true;
    }
    return false;
  });
  await page.goto('/profile');
  await page.getByRole('button', { name: 'Подать заявку на эксперта', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/reviewer-applications/${applicationId}$`));
  expect(body).toEqual({
    competency_id: skill.competency_id,
    requested_grade: 'expert',
    expected_user_competency_version: 12,
  });
});

test('expert reviews scoped portfolio, retains text on stale decision and confirms a refreshed version', async ({
  page,
}) => {
  let version = 1;
  let attempts = 0;
  let completed = false;
  const bodies: Record<string, unknown>[] = [];
  await setup(
    page,
    async (route, url) => {
      if (url.pathname === `/api/v1/reviewer-applications/${applicationId}`) {
        await route.fulfill({
          json: {
            ...application,
            version,
            current_user_competency_version: version === 1 ? 12 : 13,
            status: completed ? 'approved' : 'pending',
            can_assess: !completed,
          },
        });
        return true;
      }
      if (url.pathname === `/api/v1/reviewer-applications/${applicationId}/assessments`) {
        attempts++;
        bodies.push(route.request().postDataJSON());
        if (attempts === 1) {
          version = 2;
          await route.fulfill({ status: 409, json: { code: 'STALE_VERSION' } });
        } else {
          completed = true;
          await route.fulfill({ status: 201, json: { id: 'assessment-one' } });
        }
        return true;
      }
      return false;
    },
    true,
  );
  await page.goto('/expert/applications');
  await page.getByRole('link', { name: /SQL.*Ожидает эксперта/ }).click();
  await page.getByRole('button', { name: 'Открыть ревью', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Ревью из портфолио' }).getByText('Точные рекомендации'),
  ).toBeVisible();
  const feedback = page.getByRole('textbox', { name: 'Комментарий эксперта', exact: true });
  await feedback.fill('Сильное портфолио и точные рекомендации.');
  await page.getByRole('button', { name: 'Отправить решение эксперта', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить решение эксперта', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Данные изменились');
  await expect(feedback).toHaveValue('Сильное портфолио и точные рекомендации.');
  await expect(
    page.getByRole('button', { name: 'Отправить решение эксперта', exact: true }),
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Сверить состояние заявки', exact: true }).click();
  await page.getByRole('button', { name: 'Отправить решение эксперта', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить решение эксперта', exact: true }).click();
  await expect(page.getByText('Решение эксперта сохранено.')).toBeVisible();
  expect(
    bodies.map(body => [
      body['expected_application_version'],
      body['expected_user_competency_version'],
    ]),
  ).toEqual([
    [1, 12],
    [2, 13],
  ]);
  await noOverflow(page);
});

test('assessment navigation cancellation preserves the comment', async ({ page }) => {
  await setup(page, undefined, true);
  await page.goto(`/reviewer-applications/${applicationId}`);
  const feedback = page.getByRole('textbox', { name: 'Комментарий эксперта', exact: true });
  await feedback.fill('Несохранённый комментарий');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: 'Все заявки', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/reviewer-applications/${applicationId}$`));
  await expect(feedback).toHaveValue('Несохранённый комментарий');
});

test('uncertain readiness assessment restores and replays exact feedback and versions', async ({
  page,
}) => {
  const requests: { key: string; body: unknown }[] = [];
  let completed = false;
  await setup(
    page,
    async (route, url) => {
      if (url.pathname === `/api/v1/reviewer-applications/${applicationId}`) {
        await route.fulfill({
          json: {
            ...application,
            status: completed ? 'approved' : 'pending',
            can_assess: !completed,
          },
        });
        return true;
      }
      if (url.pathname === `/api/v1/reviewer-applications/${applicationId}/assessments`) {
        requests.push({
          key: route.request().headers()['idempotency-key'],
          body: route.request().postDataJSON(),
        });
        if (requests.length === 1)
          await route.fulfill({ status: 503, json: { code: 'DEPENDENCY_UNAVAILABLE' } });
        else {
          completed = true;
          await route.fulfill({ status: 201, json: { id: 'assessment-one' } });
        }
        return true;
      }
      return false;
    },
    true,
  );
  await page.goto(`/reviewer-applications/${applicationId}`);
  const feedback = page.getByRole('textbox', { name: 'Комментарий эксперта', exact: true });
  await feedback.fill('Исходный комментарий без потери текста.');
  await page.getByRole('button', { name: 'Отправить решение эксперта', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить решение эксперта', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Повторить отправку решения', exact: true }),
  ).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await expect(feedback).toHaveValue('Исходный комментарий без потери текста.');
  await expect(feedback).toHaveAttribute('readonly', '');
  await page.getByRole('button', { name: 'Повторить отправку решения', exact: true }).click();
  await expect(page.getByText('Решение эксперта сохранено.')).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[0].key).toBeTruthy();
  expect(requests[0]).toEqual(requests[1]);
});

test('review rights do not grant expert routes and keep ordinary review navigation', async ({
  page,
}) => {
  const expertRequests: string[] = [];
  await setup(page, async (_route, url) => {
    if (
      url.pathname === '/api/v1/reviewer-applications' ||
      url.pathname.includes('/review-assignments/quality-one')
    )
      expertRequests.push(url.pathname);
    return false;
  });
  for (const path of ['/expert/applications', '/expert']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/catalog\?reviewAccess=denied$/);
  }
  expect(expertRequests).toEqual([]);
  await page.goto('/reviews');
  await expect(page).toHaveURL(/\/reviews$/);
  await expect(
    page.getByRole('heading', { name: 'Обычное ревью', exact: true, level: 1 }),
  ).toBeVisible();
  if ((page.viewportSize()?.width ?? 1440) <= 620)
    await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
  const navigation = page.getByRole('navigation', { name: 'Основная навигация', exact: true });
  await expect(navigation.getByRole('link')).toHaveText(['Каталог кейсов', 'В работе', 'Ревью']);
  await expect(navigation.getByRole('link', { name: 'Ревью', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
});
