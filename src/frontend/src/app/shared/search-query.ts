import { debounce, distinctUntilChanged, map, Observable, of, timer } from 'rxjs';

export function normalizeSearch(value: string): string {
  return value.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');
}

/** Clearing is immediate and cancels any pending query. */
export function searchQuery(changes: Observable<string>): Observable<string> {
  return changes.pipe(
    map(normalizeSearch),
    distinctUntilChanged(),
    debounce(query => (query ? timer(300) : of(0))),
  );
}
