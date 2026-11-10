import { expect, Page, Route, test } from '@playwright/test';
import type { Assignment, Draft, Review, Workspace } from '../../src/app/api/review.service';
import { fixturePassword, signInFixture } from './fixtures/auth-layout';

type Role = 'reviewer' | 'expert';
type Account = { id: string; email: string; role: Role };
type Assessment = {
  assignment_id: string;
  revision_id: string;
  decision: 'confirmed' | 'needs_revision';
  score: number;
  feedback: string;
  expected_draft_version: number;
};
const account = (role: Role, suffix = 'a'): Account => ({
  id: `${role}-${suffix}`,
  email: `${role}-${suffix}@example.com`,
  role,
});
const taskId = (owner: Account): string =>
  `${owner.role === 'expert' ? 'quality' : 'review'}-${owner.id.slice(-1)}`;
const taskPath = (owner: Account): string =>
  `/${owner.role === 'expert' ? 'expert' : 'reviews'}/${taskId(owner)}`;
const privateText = (owner: Account): string => `Сохранённый частный отзыв ${owner.id}`;
const caseTitle = (owner: Account): string => `Задание для ${owner.id}`;

/** Enforces task ownership for every fixture read/write; nothing reaches a real API. */
async function reviewAccessFixture(page: Page) {
  const accounts = ['a', 'b'].flatMap(suffix => [
    account('reviewer', suffix),
    account('expert', suffix),
  ]);
  const tokens = new Map<string, Account>();
  const mutations: { path: string; method: string; body: unknown }[] = [];
  const assessments: Assessment[] = [];
  const unexpected: string[] = [];
  const denied: string[] = [];
  const revoked: string[] = [];
  const attachments = {
    version: 1,
    items: [],
    total_bytes: 0,
    max_file_bytes: 10 * 1024 * 1024,
    max_total_bytes: 50 * 1024 * 1024,
    max_files: 100,
  };
  let sequence = 0;
  const tasks = new Map(
    accounts.map(owner => {
      const assignment: Assignment = {
        id: taskId(owner),
        kind: owner.role === 'expert' ? 'quality_check' : 'review',
        review_id: owner.role === 'expert' ? `source-${owner.id}` : null,
        revision_id: owner.role === 'expert' ? `revision-${owner.id}` : null,
        submission_id: `submission-${owner.id}`,
        status: 'assigned',
        lease_expires_at: '2030-10-18T10:00:00Z',
      };
      const draft: Draft = {
        version: 1,
        score: 80,
        verdict: 'approved',
        feedback: {
          strengths: privateText(owner),
          problems: 'Проверить граничные значения.',
          next_steps: 'Добавить тесты.',
        },
        comments: [],
      };
      const workspace: Workspace = {
        assignment,
        draft,
        case: {
          title: caseTitle(owner),
          description: 'Проверьте расчёт месячной выручки.',
          expected_result: 'SQL-запрос и пояснение.',
        },
      };
      const source: Review & { workspace: Workspace } = {
        id: `source-${owner.id}`,
        assignment_id: `original-${owner.id}`,
        reviewer_id: `author-${owner.id}`,
        submission_id: assignment.submission_id,
        status: 'pending_quality',
        current_revision: {
          id: `revision-${owner.id}`,
          score: draft.score,
          verdict: draft.verdict,
          feedback: draft.feedback,
          comments: [],
        },
        workspace,
      };
      return [assignment.id, { owner, assignment, workspace, source }] as const;
    }),
  );
  const requestAccount = (route: Route): Account | undefined =>
    tokens.get(
      route
        .request()
        .headers()
        ['authorization']?.replace(/^Bearer /, '') ?? '',
    );
  await page.route('**/auth/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/auth/sign_in/email') {
      const body = route.request().postDataJSON() as { email: string; password: string };
      expect(body.password).toBe(fixturePassword);
      const owner = accounts.find(value => value.email === body.email);
      if (!owner) return route.fulfill({ status: 401, json: { code: 'INVALID_CREDENTIALS' } });
      const token = `review-access-session-${++sequence}`;
      tokens.set(token, owner);
      return route.fulfill({ json: { access_token: token, token_type: 'bearer' } });
    }
    const owner = requestAccount(route);
    if (!owner) return route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } });
    if (path === '/auth/me')
      return route.fulfill({
        json: { id: owner.id, role: 'user', capabilities: ['participant'] },
      });
    if (path === '/auth/sign_out') {
      revoked.push(owner.id);
      tokens.delete(
        route
          .request()
          .headers()
          ['authorization'].replace(/^Bearer /, ''),
      );
      return route.fulfill({ status: 204 });
    }
    unexpected.push(path);
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const owner = requestAccount(route);
    if (method !== 'GET') mutations.push({ path, method, body: route.request().postDataJSON() });
    if (!owner) return route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED' } });
    if (path === '/api/v1/me/review-mode')
      return route.fulfill({
        json: {
          enabled: true,
          status: 'available',
          can_review: true,
          can_assess_readiness: owner.role === 'expert',
          can_quality_review: owner.role === 'expert',
        },
      });
    if (path === '/api/v1/me/wallet')
      return route.fulfill({ json: { settled: 10, reserved: 0, available: 10, unit: 'credit' } });
    if (path === '/api/v1/me/review-assignments')
      return route.fulfill({
        json: [...tasks.values()]
          .filter(value => value.owner.id === owner.id)
          .map(value => value.assignment),
      });
    if (path === '/api/v1/me/reviews' || path === '/api/v1/me/probation')
      return route.fulfill({ json: { items: [], next_offset: null } });
    if (path === '/api/v1/cases') return route.fulfill({ json: [] });
    const assignmentMatch = path.match(/^\/api\/v1\/review-assignments\/([^/]+)(.*)$/);
    if (assignmentMatch) {
      const task = tasks.get(assignmentMatch[1]);
      if (!task || task.owner.id !== owner.id) {
        denied.push(path);
        return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
      }
      if (method === 'GET') {
        switch (assignmentMatch[2]) {
          case '':
            return route.fulfill({ json: task.assignment });
          case '/workspace':
            return route.fulfill({ json: task.workspace });
          case '/source-review':
            return route.fulfill({ json: task.source });
          case '/files':
          case '/diff':
            return route.fulfill({ json: { entries: [], next_offset: null } });
          case '/attachments':
          case '/submission-attachments':
          case '/source-attachments':
            return route.fulfill({ json: attachments });
        }
      }
    }
    const assessmentMatch = path.match(/^\/api\/v1\/reviews\/([^/]+)\/quality-assessments$/);
    if (assessmentMatch) {
      const task = [...tasks.values()].find(value => value.source.id === assessmentMatch[1]);
      if (!task || task.owner.id !== owner.id || owner.role !== 'expert') {
        denied.push(path);
        return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
      }
      if (method === 'GET') return route.fulfill({ json: { items: [], next_offset: null } });
      if (method === 'POST') {
        const body = route.request().postDataJSON() as Assessment;
        expect(body.assignment_id).toBe(task.assignment.id);
        expect(body.revision_id).toBe(task.source.current_revision.id);
        expect(route.request().headers()['idempotency-key']).toBeTruthy();
        assessments.push(body);
        return route.fulfill({ json: { id: `assessment-${assessments.length}`, ...body } });
      }
    }
    const assessmentAttachments = path.match(
      /^\/api\/v1\/quality-assessments\/assessment-(\d+)\/attachments$/,
    );
    if (assessmentAttachments && method === 'GET') {
      const assessment = assessments[Number(assessmentAttachments[1]) - 1];
      const task = assessment ? tasks.get(assessment.assignment_id) : undefined;
      if (task?.owner.id === owner.id) return route.fulfill({ json: attachments });
      denied.push(path);
      return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
    }
    unexpected.push(`${method} ${path}`);
    return route.fulfill({ status: 404, json: { code: 'NOT_FOUND' } });
  });
  return { mutations, assessments, unexpected, denied, revoked };
}

async function expectTask(page: Page, owner: Account): Promise<void> {
  await expect(page).toHaveURL(new RegExp(`${taskPath(owner)}$`));
  await expect(page.getByRole('heading', { name: caseTitle(owner), exact: true })).toBeVisible();
  if (owner.role === 'expert') {
    await page.getByRole('button', { name: 'Исходное ревью', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Исходное ревью', exact: true })).toContainText(
      privateText(owner),
    );
    await expect(page.getByRole('form', { name: 'Заключение эксперта' })).toBeVisible();
  } else {
    await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
    await expect(page.getByLabel('Сильные стороны', { exact: true })).toHaveValue(
      privateText(owner),
    );
  }
}
async function loginTask(page: Page, owner: Account): Promise<void> {
  await page.goto(taskPath(owner));
  await expect(page.getByRole('heading', { level: 1, name: 'Вход', exact: true })).toBeVisible();
  expect(new URL(page.url()).searchParams.get('returnUrl')).toBe(taskPath(owner));
  await signInFixture(page, owner.email);
  await expectTask(page, owner);
}
async function expectUnavailable(page: Page, previous: Account): Promise<void> {
  await expect(page.getByRole('alert')).toContainText('Данные пока не найдены.');
  await expect(page.getByRole('form', { name: 'Заключение эксперта' })).toHaveCount(0);
  await expect(page.getByLabel('Сильные стороны', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Исходное ревью', exact: true })).toHaveCount(0);
  await expect(page.getByText(privateText(previous), { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Сохранить экспертное решение' })).toHaveCount(0);
}

test('quality rejects empty, out-of-range, low confirmation and whitespace values without mutation', async ({
  page,
}) => {
  const state = await reviewAccessFixture(page);
  await loginTask(page, account('expert'));
  const save = page.getByRole('button', { name: 'Сохранить экспертное решение', exact: true });
  const score = page.getByLabel('Оценка качества ревью', { exact: true });
  const feedback = page.getByLabel('Комментарий эксперта', { exact: true });
  await expect(save).toBeDisabled();
  await feedback.fill('Проверены аргументы и рекомендации.');
  for (const value of ['', '-1', '101']) {
    await score.fill(value);
    await expect(save).toBeDisabled();
    await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toHaveCount(0);
  }
  for (const value of ['0', '69']) {
    await score.fill(value);
    await save.click();
    await page.getByRole('button', { name: 'Подтвердить экспертное решение', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('не ниже 70');
    expect(state.mutations).toEqual([]);
    await page.getByRole('button', { name: 'Отмена экспертного решения', exact: true }).click();
  }
  await score.fill('85');
  await feedback.fill('   \n  ');
  await save.click();
  await page.getByRole('button', { name: 'Подтвердить экспертное решение', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('содержательный комментарий');
  await expect(page.getByRole('region', { name: 'Исходное ревью', exact: true })).toContainText(
    privateText(account('expert')),
  );
  expect(state.mutations).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

// The API accepts Decimal 0..100; confirmation requires >=70, with no integer-only rule.
for (const boundary of [
  { decision: 'confirmed', score: 70 },
  { decision: 'confirmed', score: 70.5 },
  { decision: 'confirmed', score: 100 },
  { decision: 'needs_revision', score: 0 },
] as const) {
  test(`quality ${boundary.decision} score ${boundary.score}: cancel, edit and keyboard confirm exactly once`, async ({
    page,
  }) => {
    const state = await reviewAccessFixture(page);
    const owner = account('expert');
    await loginTask(page, owner);
    await page.getByLabel('Решение эксперта', { exact: true }).click();
    await page
      .getByRole('listbox')
      .getByRole('option', {
        name:
          boundary.decision === 'confirmed' ? 'Подтвердить ревью' : 'Вернуть ревью на доработку',
        exact: true,
      })
      .click();
    await page.getByLabel('Оценка качества ревью', { exact: true }).fill(`${boundary.score}`);
    const feedback = page.getByLabel('Комментарий эксперта', { exact: true });
    await feedback.fill('Первоначальный комментарий.');
    const save = page.getByRole('button', { name: 'Сохранить экспертное решение', exact: true });
    await save.click();
    await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toBeVisible();
    expect(state.mutations).toEqual([]);
    await page.getByRole('button', { name: 'Отмена экспертного решения', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Подтверждение публикации' })).toHaveCount(0);
    await expect(feedback).toHaveValue('Первоначальный комментарий.');
    await feedback.fill('Уточнённые замечания с проверенными примерами.');
    await save.focus();
    await page.keyboard.press('Enter');
    const confirm = page.getByRole('button', {
      name: 'Подтвердить экспертное решение',
      exact: true,
    });
    await expect(confirm).toBeVisible();
    expect(state.mutations).toEqual([]);
    await confirm.focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('status').filter({ hasText: 'Экспертная оценка сохранена' }),
    ).toContainText(boundary.decision === 'confirmed' ? 'Подтверждено' : 'Нужна доработка ревью');
    await expect(page.getByRole('form', { name: 'Заключение эксперта' })).toHaveCount(0);
    expect(state.assessments).toEqual([
      {
        assignment_id: taskId(owner),
        revision_id: `revision-${owner.id}`,
        ...boundary,
        feedback: 'Уточнённые замечания с проверенными примерами.',
        expected_draft_version: 1,
      },
    ]);
    expect(state.mutations).toHaveLength(1);
    expect(state.mutations[0].method).toBe('POST');
    await expect(page.getByRole('region', { name: 'Исходное ревью', exact: true })).toContainText(
      privateText(owner),
    );
    expect(state.unexpected).toEqual([]);
  });
}

for (const role of ['reviewer', 'expert'] as const) {
  test(`${role} direct task restores after login, reload and back; unknown and foreign IDs show no private content`, async ({
    page,
  }) => {
    const state = await reviewAccessFixture(page);
    const owner = account(role);
    await loginTask(page, owner);
    await page.reload();
    await expectTask(page, owner);
    await page.goto(role === 'expert' ? '/expert' : '/reviews');
    await expect(
      page.getByRole('heading', {
        level: 1,
        name: role === 'expert' ? 'Экспертное ревью' : 'Обычное ревью',
        exact: true,
      }),
    ).toBeVisible();
    await page.goBack();
    await expectTask(page, owner);
    for (const id of ['missing', taskId(account(role, 'b'))]) {
      await page.goto(`/${role === 'expert' ? 'expert' : 'reviews'}/${id}`);
      await expectUnavailable(page, owner);
      expect(state.denied).toContain(`/api/v1/review-assignments/${id}`);
    }
    expect(state.mutations).toEqual([]);
    expect(state.unexpected).toEqual([]);
  });

  test(`${role} logout and account switch discard private forms and cannot mutate a foreign task`, async ({
    page,
  }) => {
    const state = await reviewAccessFixture(page);
    const first = account(role),
      second = account(role, 'b');
    await loginTask(page, first);
    const privateDraft = `Несохранённое заключение ${first.id}`;
    await page
      .getByLabel(role === 'expert' ? 'Комментарий эксперта' : 'Сильные стороны', { exact: true })
      .fill(privateDraft);
    if ((page.viewportSize()?.width ?? 1440) <= 620)
      await page.getByRole('button', { name: 'Показать боковую панель', exact: true }).click();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Выйти из аккаунта', exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(state.revoked).toEqual([first.id]);
    await page.goto(taskPath(first));
    await signInFixture(page, second.email);
    await expect(page).toHaveURL(new RegExp(`${taskPath(first)}$`));
    await expectUnavailable(page, first);
    await expect(page.getByText(privateDraft, { exact: true })).toHaveCount(0);
    expect(state.denied).toContain(`/api/v1/review-assignments/${taskId(first)}`);
    await page.goto(taskPath(second));
    await expectTask(page, second);
    if (role === 'expert')
      await expect(page.getByLabel('Комментарий эксперта', { exact: true })).toHaveValue('');
    await expect(page.getByText(privateText(first), { exact: true })).toHaveCount(0);
    expect(state.mutations).toEqual([]);
    expect(state.unexpected).toEqual([]);
  });
}
