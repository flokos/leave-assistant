// Bridge between the OIDC provider (React context) and the plain fetch client: the current access token getter.
type TokenGetter = () => string | undefined

let getter: TokenGetter = () => undefined

export function setAccessTokenGetter(next: TokenGetter | null): void {
  getter = next ?? (() => undefined)
}

export function currentAccessToken(): string | undefined {
  return getter()
}

export const authEnabled = import.meta.env.VITE_AUTH_ENABLED === 'true'
