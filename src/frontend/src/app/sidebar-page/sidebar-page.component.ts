import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  inject,
  Injector,
  signal,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TuiButton, TuiTextfield } from '@taiga-ui/core';

import { SidebarComponent } from '../ui-kit/sidebar/sidebar.component';
import { SidebarItem } from '../ui-kit/sidebar/sidebar.types';

interface Project {
  readonly id: number;
  readonly name: string;
  readonly description: string;
  readonly owner: string;
  readonly status: 'В работе' | 'На проверке' | 'Завершён';
}

type WorkspaceSection = 'projects' | 'team' | 'settings';

@Component({
  selector: 'app-sidebar-page',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, TuiButton, TuiTextfield, SidebarComponent],
  templateUrl: './sidebar-page.component.html',
  styleUrl: './sidebar-page.component.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SidebarPageComponent {
  private readonly injector = inject(Injector);
  private readonly fragment = toSignal(inject(ActivatedRoute).fragment);
  private readonly heading = viewChild.required<ElementRef<HTMLHeadingElement>>('heading');
  private readonly projectNameInput = viewChild<ElementRef<HTMLInputElement>>('projectNameInput');
  private readonly createButton = viewChild<ElementRef<HTMLButtonElement>>('createButton');
  readonly expanded = signal(false);
  readonly mobileOpen = signal(false);
  readonly section = computed<WorkspaceSection>(() => {
    const fragment = this.fragment();
    return fragment === 'team' || fragment === 'settings' ? fragment : 'projects';
  });
  readonly sectionTitle = computed(
    () => ({ projects: 'Проекты', team: 'Команда', settings: 'Настройки' })[this.section()],
  );
  readonly navigation: readonly SidebarItem[] = [
    {
      id: 'projects',
      label: 'Проекты',
      href: '#projects',
      icon: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z',
    },
    {
      id: 'team',
      label: 'Команда',
      href: '#team',
      icon: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75',
    },
    {
      id: 'settings',
      label: 'Настройки',
      href: '#settings',
      icon: 'M4 7h16 M4 17h16 M8 4v6 M16 14v6',
    },
  ];
  readonly footerNavigation: readonly SidebarItem[] = [
    {
      id: 'ui-kit',
      label: 'UI-kit',
      href: '/ui-kit',
      icon: 'm12 3 9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',
    },
  ];
  readonly projects = signal<readonly Project[]>([
    {
      id: 1,
      name: 'Мобильное приложение',
      description: 'Обновление главного экрана и навигации',
      owner: 'Анна Смирнова',
      status: 'В работе',
    },
    {
      id: 2,
      name: 'Личный кабинет',
      description: 'Профиль, документы и уведомления',
      owner: 'Михаил Орлов',
      status: 'На проверке',
    },
    {
      id: 3,
      name: 'Регистрация новых пользователей',
      description: 'Короткий путь от знакомства до первого действия',
      owner: 'Анна Смирнова',
      status: 'В работе',
    },
    {
      id: 4,
      name: 'Библиотека компонентов',
      description: 'Общие элементы интерфейса для команды',
      owner: 'Даша Волкова',
      status: 'В работе',
    },
    {
      id: 5,
      name: 'Центр поддержки',
      description: 'Ответы на частые вопросы',
      owner: 'Михаил Орлов',
      status: 'Завершён',
    },
  ]);
  readonly search = new FormControl('', { nonNullable: true });
  private readonly searchValue = toSignal(this.search.valueChanges, { initialValue: '' });
  readonly filteredProjects = computed(() => {
    const query = this.searchValue().trim().toLocaleLowerCase('ru');
    return this.projects().filter(project =>
      `${project.name} ${project.description}`.toLocaleLowerCase('ru').includes(query),
    );
  });
  readonly selectedProjectId = signal(1);
  readonly selectedProject = computed(() =>
    this.projects().find(project => project.id === this.selectedProjectId()),
  );
  readonly creating = signal(false);
  readonly projectName = new FormControl('', {
    nonNullable: true,
    validators: [Validators.required, Validators.pattern(/\S/), Validators.maxLength(80)],
  });
  readonly projectDescription = new FormControl('', {
    nonNullable: true,
    validators: [Validators.maxLength(160)],
  });
  readonly projectForm = new FormGroup({
    name: this.projectName,
    description: this.projectDescription,
  });
  readonly members = [
    { name: 'Анна Смирнова', role: 'Дизайнер', initials: 'АС' },
    { name: 'Михаил Орлов', role: 'Разработчик', initials: 'МО' },
    { name: 'Даша Волкова', role: 'Менеджер проекта', initials: 'ДВ' },
  ];
  readonly workspaceName = new FormControl('Продуктовая команда', {
    nonNullable: true,
    validators: [Validators.required, Validators.pattern(/\S/), Validators.maxLength(60)],
  });
  readonly workspaceForm = new FormGroup({ name: this.workspaceName });
  readonly savedWorkspaceName = signal('Продуктовая команда');
  readonly notice = signal('');

  onNavigate(item: SidebarItem): void {
    if (this.mobileOpen() && item.href.startsWith('#')) {
      afterNextRender(() => this.heading().nativeElement.focus(), { injector: this.injector });
    }
  }

  selectProject(project: Project): void {
    this.selectedProjectId.set(project.id);
    this.creating.set(false);
  }

  startProject(): void {
    this.creating.set(true);
    this.projectName.reset();
    this.projectDescription.reset();
    this.notice.set('');
    afterNextRender(() => this.projectNameInput()?.nativeElement.focus(), {
      injector: this.injector,
    });
  }

  cancelProject(): void {
    this.creating.set(false);
    this.createButton()?.nativeElement.focus();
  }

  createProject(): void {
    this.projectName.markAsTouched();
    this.projectDescription.markAsTouched();
    if (this.projectName.invalid || this.projectDescription.invalid) return;
    const project: Project = {
      id: Math.max(...this.projects().map(item => item.id)) + 1,
      name: this.projectName.value.trim(),
      description: this.projectDescription.value.trim() || 'Описание пока не добавлено',
      owner: 'Анна Смирнова',
      status: 'В работе',
    };
    this.projects.update(projects => [...projects, project]);
    this.selectedProjectId.set(project.id);
    this.search.reset();
    this.creating.set(false);
    this.notice.set(`Проект «${project.name}» добавлен в пример.`);
    this.createButton()?.nativeElement.focus();
  }

  saveWorkspace(): void {
    this.workspaceName.markAsTouched();
    if (this.workspaceName.invalid) return;
    const name = this.workspaceName.value.trim();
    this.workspaceName.setValue(name);
    this.savedWorkspaceName.set(name);
    this.notice.set('Название изменено в этой вкладке.');
  }
}
