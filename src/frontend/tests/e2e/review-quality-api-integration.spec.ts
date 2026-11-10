import { expect, test, Page } from '@playwright/test';

async function role(page: Page, label: string): Promise<void> {
  await page.goto('/login');
  await page.getByRole('button', { name: label, exact: true }).click();
  await expect(page).toHaveURL(label === 'Эксперт' ? /\/expert$/ : /\/catalog$/);
}
async function claim(page: Page): Promise<{ id: string; submission_id: string; kind: string }> {
  await page.goto(new URL(page.url()).pathname.startsWith('/expert') ? '/expert' : '/reviews');
  const response = page.waitForResponse(
    r => r.url().endsWith('/api/v1/me/review-assignments') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Начать ревью', exact: true }).click();
  const r = await response;
  expect([200, 201]).toContain(r.status());
  return (await r.json()) as { id: string; submission_id: string; kind: string };
}

test('provisional review → private expert correction → new revision → confirmation and probation', async ({
  page,
  request,
}) => {
  test.setTimeout(240000);
  const password = 'Local-demo-2026!',
    email = `quality-new-${crypto.randomUUID()}@example.com`;
  const login = await request.post('/auth/sign_in/email', {
    data: { email: 'organizer.local@example.com', password },
  });
  expect(login.status()).toBe(200);
  const organizer = ((await login.json()) as { access_token: string }).access_token;
  const signup = await request.post('/auth/sign_up/email', { data: { email, password } });
  expect(signup.status()).toBe(200);
  const student = ((await signup.json()) as { access_token: string }).access_token;
  const call = async (
    method: string,
    path: string,
    token: string,
    data?: unknown,
  ): Promise<Record<string, unknown>> => {
    const r = await request.fetch(path, {
      method,
      headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': crypto.randomUUID() },
      ...(data === undefined ? {} : { data }),
    });
    expect(r.ok(), `${method} ${path}: ${r.status()}`).toBeTruthy();
    return (await r.json()) as Record<string, unknown>;
  };
  const skillsResponse = await request.get('/api/v1/competencies', {
    headers: { Authorization: `Bearer ${organizer}` },
  });
  const skills = (await skillsResponse.json()) as { id: string; name: string }[];
  const sql = skills.find(skill => skill.name === 'SQL');
  expect(sql).toBeDefined();
  const performerLogin = await request.post('/auth/sign_in/email', {
    data: { email: 'performer.local@example.com', password },
  });
  expect(performerLogin.status()).toBe(200);
  const performerToken = (await performerLogin.json()).access_token as string;
  const reviewerLogin = await request.post('/auth/sign_in/email', {
    data: { email: 'review-plus.local@example.com', password },
  });
  expect(reviewerLogin.status()).toBe(200);
  const reviewerToken = (await reviewerLogin.json()).access_token as string;
  await call('PUT', '/api/v1/me/review-mode', reviewerToken, { enabled: true });
  for (let index = 0; index < 2; index++) {
    const preparation = await call('POST', '/api/v1/admin/cases', organizer, {
      title: `Испытательный период: подготовка ${crypto.randomUUID()}`,
      description: 'Верните 2.',
      expected_result: '2',
      competencies: [{ competency_id: sql!.id, required_value: 0, weight: 0.25 }],
    });
    const preparationPath = `/api/v1/admin/cases/${String(preparation['id'])}`;
    await call('POST', preparationPath + '/files?expected_case_version=1', organizer, {
      path: 'task.sql',
      content: 'SELECT 1;',
    });
    const preparationRef = await call(
      'PUT',
      preparationPath + '/reference-solution?expected_case_version=2',
      organizer,
    );
    await call(
      'PATCH',
      preparationPath + '/reference-solution?expected_case_version=3',
      organizer,
      { expected_version: 1, expected_commit_sha: preparationRef['head_commit_sha'], ready: true },
    );
    await call('PATCH', preparationPath, organizer, { expected_version: 4, status: 'published' });
    const preparationProject = await call('POST', '/api/v1/me/projects', performerToken, {
      case_id: preparation['id'],
    });
    const preparationProjectPath = `/api/v1/projects/${String(preparationProject['id'])}`;
    const preparationFile = await call(
      'GET',
      preparationProjectPath + '/files/content?path=task.sql',
      performerToken,
    );
    const preparationSaved = await call(
      'PUT',
      preparationProjectPath + '/files/content?path=task.sql',
      performerToken,
      { sha: preparationFile['sha'], content: 'SELECT 2;' },
    );
    const preparationSubmission = await call(
      'POST',
      preparationProjectPath + '/submissions',
      performerToken,
      { expected_commit_sha: preparationSaved['commit_sha'] },
    );
    const preparationAssignment = await call(
      'POST',
      '/api/v1/me/review-assignments',
      reviewerToken,
    );
    expect(preparationAssignment['submission_id']).toBe(preparationSubmission['id']);
    const preparationAssignmentPath = `/api/v1/review-assignments/${String(preparationAssignment['id'])}`;
    const preparationDraft = await call(
      'PUT',
      preparationAssignmentPath + '/draft',
      reviewerToken,
      {
        expected_version: 1,
        score: 85,
        verdict: 'approved',
        feedback: {
          strengths: 'Верный запрос.',
          problems: 'Ошибок нет.',
          next_steps: 'Добавьте тест.',
        },
        comments: [],
      },
    );
    await call('POST', preparationAssignmentPath + '/reviews', reviewerToken, {
      expected_draft_version: preparationDraft['version'],
    });
  }
  const study = await call('POST', '/api/v1/admin/cases', organizer, {
    title: 'Экспертное подтверждение SQL',
    description: 'Верните число два SQL-запросом.',
    expected_result: 'Число 2',
    competencies: [{ competency_id: sql!.id, required_value: 0, weight: 0.25 }],
  });
  const root = `/api/v1/admin/cases/${String(study['id'])}`;
  await call('POST', root + '/files?expected_case_version=1', organizer, {
    path: 'task.sql',
    content: 'SELECT 1;\n',
  });
  const reference = await call(
    'PUT',
    root + '/reference-solution?expected_case_version=2',
    organizer,
  );
  await call('PATCH', root + '/reference-solution?expected_case_version=3', organizer, {
    expected_version: 1,
    expected_commit_sha: reference['head_commit_sha'],
    ready: true,
  });
  await call('PATCH', root, organizer, { expected_version: 4, status: 'published' });
  const project = await call('POST', '/api/v1/me/projects', student, { case_id: study['id'] });
  const projectPath = `/api/v1/projects/${String(project['id'])}`;
  const file = await call('GET', projectPath + '/files/content?path=task.sql', student);
  const saved = await call('PUT', projectPath + '/files/content?path=task.sql', student, {
    sha: file['sha'],
    content: 'SELECT 2;\n',
  });
  const submission = await call('POST', projectPath + '/submissions', student, {
    expected_commit_sha: saved['commit_sha'],
  });
  await role(page, 'Исполнитель');
  const probationLogin = await request.post('/auth/sign_in/email', {
    data: { email: 'performer.local@example.com', password },
  });
  expect(probationLogin.status()).toBe(200);
  const probationToken = ((await probationLogin.json()) as { access_token: string }).access_token;
  const probation = await request.get('/api/v1/me/probation', {
    headers: { Authorization: `Bearer ${probationToken}` },
  });
  expect(probation.status()).toBe(200);
  expect(((await probation.json()) as { items: { required_successes: number }[] }).items).toEqual(
    expect.arrayContaining([expect.objectContaining({ required_successes: 3 })]),
  );
  const task = await claim(page);
  expect(task.kind).toBe('review');
  expect(task.submission_id).toBe(submission['id']);
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page.getByLabel('Оценка от 0 до 100').fill('80');
  await page.getByLabel('Вердикт', { exact: true }).click();
  await page.getByRole('listbox').getByRole('option', { name: 'Принято', exact: true }).click();
  await page
    .getByLabel('Сильные стороны', { exact: true })
    .fill('Запрос возвращает требуемое число.');
  await page.getByLabel('Проблемы', { exact: true }).fill('Нужно пояснить подход.');
  await page.getByLabel('Следующие шаги', { exact: true }).fill('Добавить обоснование.');
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(page.getByText('Черновик сохранён на сервере', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  const completed = page.waitForResponse(
    r =>
      r.url().endsWith(`/review-assignments/${task.id}/reviews`) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  const result = await completed;
  expect(result.status()).toBe(201);
  const review = (await result.json()) as { id: string };
  await expect(page).toHaveURL(/\/review-results\//);
  await expect(page.getByRole('region', { name: 'Ревью на проверке', exact: true })).toContainText(
    'Ожидает эксперта',
  );
  const hidden = await request.get(`/api/v1/submissions/${String(submission['id'])}/feedback`, {
    headers: { Authorization: `Bearer ${student}` },
  });
  expect(((await hidden.json()) as { reviews: unknown[] }).reviews).toEqual([]);
  await role(page, 'Эксперт');
  const quality = await claim(page);
  expect(quality.kind).toBe('quality_check');
  await expect(
    page.getByRole('heading', { name: 'Экспертная проверка ревью', exact: true }),
  ).toBeVisible();
  await page.getByLabel('Решение эксперта', { exact: true }).click();
  await page
    .getByRole('listbox')
    .getByRole('option', { name: 'Вернуть ревью на доработку', exact: true })
    .click();
  await page.getByLabel('Оценка качества ревью', { exact: true }).fill('60');
  await page
    .getByLabel('Комментарий эксперта', { exact: true })
    .fill('Приватно: объясните критерии и обоснуйте оценку.');
  await page.getByRole('button', { name: 'Сохранить экспертное решение', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить экспертное решение', exact: true }).click();
  await expect(page.getByText('Экспертная оценка сохранена:', { exact: false })).toBeVisible();
  await role(page, 'Исполнитель');
  await page.goto(`/review-results/${review.id}`);
  await page.getByRole('button', { name: /Нужна доработка · 60/ }).click();
  await expect(
    page.getByText('Приватно: объясните критерии и обоснуйте оценку.', { exact: true }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Открыть оценённую редакцию', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Результат ревью', exact: true })).toHaveCount(2);
  await page.getByRole('link', { name: 'Исправить ревью', exact: true }).click();
  await page.getByRole('button', { name: '3. Обратная связь', exact: true }).click();
  await page
    .getByLabel('Проблемы', { exact: true })
    .fill('Критичных ошибок нет. Проверены значение результата и отсутствие лишних строк.');
  await page
    .getByLabel('Следующие шаги', { exact: true })
    .fill('Добавьте пояснение и проверьте запрос на пустой таблице.');
  await page.getByRole('button', { name: 'Сохранить черновик', exact: true }).click();
  await expect(page.getByText('Черновик сохранён на сервере', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Отправить ревью', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить отправку ревью', exact: true }).click();
  await expect(page).toHaveURL(`/review-results/${review.id}`);
  await role(page, 'Эксперт');
  const second = await claim(page);
  expect(second.kind).toBe('quality_check');
  await page
    .getByLabel('Комментарий эксперта', { exact: true })
    .fill('Приватно: критерии раскрыты, исправленное ревью подтверждено.');
  await page.getByRole('button', { name: 'Сохранить экспертное решение', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить экспертное решение', exact: true }).click();
  await expect(page.getByText('Экспертная оценка сохранена:', { exact: false })).toBeVisible();
  await page.goto('/login');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  await expect(page).toHaveURL(/\/catalog$/);
  await page.goto(`/submissions/${String(submission['id'])}`);
  await expect(page.getByRole('region', { name: 'Опубликованное ревью' })).toContainText(
    '80 / 100',
  );
  await expect(page.getByText(/Приватно:/)).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Изменение навыков', exact: true })).toBeVisible();
  await expect(page.getByTestId('credits-balance')).toContainText('13');
  await page.goto('/profile#wallet');
  await expect(page.getByRole('list', { name: 'Ваши навыки' })).toContainText('SQL');
  await expect(page.getByText('Награда за кейс', { exact: true })).toBeVisible();
});
