/**
 * Wraps an async submit handler for an event prop. Rejections are swallowed on purpose: antd form validation
 * renders its own messages and API errors are shown by useApiMutation / ProblemAlert.
 */
export function handle(fn: () => Promise<unknown>): () => void {
  return () => {
    void fn().catch(() => undefined)
  }
}
