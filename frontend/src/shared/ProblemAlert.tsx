import { Alert } from 'antd'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../api/client'

/** Renders an RFC 9457 problem (and its `errors[]`) without interpreting it — the backend decides. */
export function ProblemAlert({ error }: { error: unknown }) {
  const { t } = useTranslation()
  if (!error) return null
  if (!(error instanceof ApiError)) {
    return <Alert type="error" showIcon message={t('errors.unexpected')} description={String(error)} />
  }
  const { problem } = error
  const code = error.code
  const title = t(`problems.${code}`, { defaultValue: problem.title })
  return (
    <Alert
      type={error.status === 403 ? 'warning' : 'error'}
      showIcon
      data-testid="problem-alert"
      message={title}
      description={
        <>
          {problem.detail && <div>{problem.detail}</div>}
          {problem.errors && problem.errors.length > 0 && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              {problem.errors.map((e, i) => (
                <li key={i}>
                  {e.code ? t(`problems.${e.code}`, { defaultValue: e.code }) : (e.loc ?? []).join('.')}
                  {e.msg ? `: ${e.msg}` : ''}
                </li>
              ))}
            </ul>
          )}
          {problem.request_id && (
            <div style={{ marginTop: 8, opacity: 0.7 }}>
              {t('errors.requestId')}: {problem.request_id}
            </div>
          )}
        </>
      }
    />
  )
}
