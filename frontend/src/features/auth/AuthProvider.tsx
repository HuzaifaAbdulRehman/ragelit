import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"

import {
  ApiError,
  api,
  type LoginCommand,
  type Organization,
} from "../../api/client"

type AuthStatus = "booting" | "anonymous" | "authenticated"

interface AuthValue {
  status: AuthStatus
  accessToken: string | null
  organizations: Organization[]
  currentOrganization: Organization | null
  login(command: LoginCommand): Promise<void>
  logout(): Promise<void>
  switchOrganization(organizationId: string): Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

function tokenOrganizationId(token: string): string | null {
  try {
    const payload = token.split(".")[1]
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/")
    return (JSON.parse(atob(normalized)) as { org?: string }).org ?? null
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting")
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [currentOrganization, setCurrentOrganization] =
    useState<Organization | null>(null)

  const establish = useCallback(async (token: string) => {
    const listed = await api.organizations(token)
    const organizationId = tokenOrganizationId(token)
    setAccessToken(token)
    setOrganizations(listed.items)
    setCurrentOrganization(
      listed.items.find((item) => item.id === organizationId) ?? null,
    )
    setStatus("authenticated")
  }, [])

  useEffect(() => {
    api
      .refresh()
      .then((result) => establish(result.access_token))
      .catch(() => setStatus("anonymous"))
  }, [establish])

  async function login(command: LoginCommand) {
    const result = await api.login(command)
    await establish(result.access_token)
  }

  async function logout() {
    if (accessToken) await api.logout(accessToken)
    setAccessToken(null)
    setOrganizations([])
    setCurrentOrganization(null)
    setStatus("anonymous")
  }

  async function switchOrganization(organizationId: string) {
    if (!accessToken) return
    const result = await api.switchOrganization(accessToken, organizationId)
    await establish(result.access_token)
  }

  return (
    <AuthContext.Provider
      value={{
        status,
        accessToken,
        organizations,
        currentOrganization,
        login,
        logout,
        switchOrganization,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error("useAuth must be used inside AuthProvider")
  return value
}

export function isAuthenticationError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}
