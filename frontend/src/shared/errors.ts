import { ApiError } from '../api/client'

/** One-line description of an error for toasts; problem codes map to i18n `problems.*`. */
export function describeError(error: unknown, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (error instanceof ApiError) {
    const title = t(`problems.${error.code}`, { defaultValue: error.problem.title })
    return error.problem.detail ? `${title}: ${error.problem.detail}` : title
  }
  return t('errors.unexpected')
}
