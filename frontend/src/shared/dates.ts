import dayjs, { type Dayjs } from 'dayjs'
import 'dayjs/locale/el'
import 'dayjs/locale/en'

/** Leave dates are civil dates (YYYY-MM-DD) and never go through timezone conversion (doc 03 §8). */
export const toIso = (d: Dayjs): string => d.format('YYYY-MM-DD')

export function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return '—'
  return dayjs(iso).locale(locale === 'el' ? 'el' : 'en').format('D MMM YYYY')
}

export function formatDateTime(iso: string | null | undefined, locale: string): string {
  if (!iso) return '—'
  return dayjs(iso).locale(locale === 'el' ? 'el' : 'en').format('D MMM YYYY HH:mm')
}

export function formatRange(start: string, end: string, locale: string): string {
  return start === end ? formatDate(start, locale) : `${formatDate(start, locale)} – ${formatDate(end, locale)}`
}

export const currentYear = (): number => dayjs().year()
