/** Day quantities arrive as numbers (multiples of 0.5); display them without trailing zeros. */
export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}
