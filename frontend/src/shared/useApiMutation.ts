import { App } from 'antd'
import { useMutation, type UseMutationOptions } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { describeError } from './errors'

/** useMutation with the project's default error toast; callers may still read `error` for inline rendering. */
export function useApiMutation<TData, TVariables>(
  options: UseMutationOptions<TData, unknown, TVariables> & { silent?: boolean },
) {
  const { message } = App.useApp()
  const { t } = useTranslation()
  return useMutation<TData, unknown, TVariables>({
    ...options,
    onError: (error, variables, onMutateResult, context) => {
      if (!options.silent) void message.error(describeError(error, t))
      options.onError?.(error, variables, onMutateResult, context)
    },
  })
}
