const pluralRules = new Intl.PluralRules('ru-RU');
const amountFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const signedAmountFormat = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 2,
  signDisplay: 'exceptZero',
});

export function formatProditsAmount(amount: number | null, showPlus = false): string {
  if (amount === null) return '—';
  const formatted = (showPlus ? signedAmountFormat : amountFormat).format(amount);
  return formatted === '-0' ? '0' : formatted;
}

export function proditsUnit(amount: number | null): string {
  const category = pluralRules.select(Math.abs(amount ?? 0));
  return category === 'one' ? 'PRODит' : category === 'few' ? 'PRODита' : 'PRODитов';
}
