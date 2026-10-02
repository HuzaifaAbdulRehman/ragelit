import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
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
  currentUserId: string | null
  workspaceRevision: number
  sessionMessage: string | null
  logoutPending: boolean
  revocationFailed: boolean
  login(command: LoginCommand): Promise<void>
  logout(): Promise<void>
  switchOrganization(organizationId: string): Promise<void>
  reloadMembership(): Promise<void>
  expireSession(): void
}
const AuthContext = createContext<AuthValue | null>(null)
function tokenIdentity(token: string): { org?: string; sub?: string } {
  try {
    return JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    )
  } catch {
    return {}
  }
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("booting")
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [organizations, setOrganizations] = useState<Organization[]>([])
  const [currentOrganization, setCurrentOrganization] =
    useState<Organization | null>(null)
  const [currentUserId, setCurrentUserId] = useState<string | null>(null)
  const [workspaceRevision, setRevision] = useState(0)
  const [sessionMessage, setMessage] = useState<string | null>(null)
  const [logoutPending, setLogoutPending] = useState(false)
  const [revocationFailed, setRevocationFailed] = useState(false)
  const transition = useRef(0)
  const authRequest = useRef<AbortController | null>(null)
  const pendingLogoutToken = useRef<string | null>(null)
  const clear = useCallback((next: AuthStatus) => {
    authRequest.current?.abort()
    authRequest.current = new AbortController()
    const ticket = ++transition.current
    setRevision((value) => value + 1)
    setAccessToken(null)
    setOrganizations([])
    setCurrentOrganization(null)
    setCurrentUserId(null)
    setStatus(next)
    return ticket
  }, [])
  const establish = useCallback(async (token: string, ticket: number) => {
    if (ticket !== transition.current) return
    const listed = await api.organizations(token, authRequest.current?.signal)
    if (ticket !== transition.current) return
    const identity = tokenIdentity(token)
    const selected = listed.items.find((item) => item.id === identity.org)
    if (!selected) throw new Error("Selected workspace unavailable")
    setAccessToken(token)
    setOrganizations(listed.items)
    setCurrentOrganization(selected)
    setCurrentUserId(identity.sub ?? null)
    setStatus("authenticated")
  }, [])
  const expireSession = useCallback(() => {
    clear("anonymous")
    setMessage("Your session expired. Please sign in again.")
  }, [clear])
  useEffect(() => {
    const ticket = clear("booting")
    api
      .refresh()
      .then((result) => establish(result.access_token, ticket))
      .catch(() => {
        if (ticket === transition.current) setStatus("anonymous")
      })
    return () => {
      ++transition.current
      authRequest.current?.abort()
    }
  }, [clear, establish])
  async function login(command: LoginCommand) {
    if (logoutPending || revocationFailed)
      throw new Error("Retry session revocation first")
    const ticket = clear("booting")
    setMessage(null)
    try {
      const result = await api.login(command)
      await establish(result.access_token, ticket)
    } catch (error) {
      if (ticket === transition.current) setStatus("anonymous")
      throw error
    }
  }
  async function logout() {
    if (logoutPending) return
    const token = accessToken ?? pendingLogoutToken.current
    const ticket = clear("anonymous")
    setLogoutPending(true)
    setMessage("Logging out. Private views have been cleared.")
    pendingLogoutToken.current = token
    try {
      if (token) await api.logout(token)
      if (ticket !== transition.current) return
      pendingLogoutToken.current = null
      setRevocationFailed(false)
      setMessage(null)
    } catch {
      if (ticket === transition.current) {
        setRevocationFailed(true)
        setMessage(
          "Session revocation failed. Private views are hidden; retry log out before leaving this browser.",
        )
      }
    } finally {
      if (ticket === transition.current) setLogoutPending(false)
    }
  }
  async function switchOrganization(organizationId: string) {
    if (!accessToken) return
    const token = accessToken
    const ticket = clear("booting")
    setMessage(null)
    try {
      const result = await api.switchOrganization(token, organizationId)
      await establish(result.access_token, ticket)
    } catch {
      if (ticket === transition.current) {
        setStatus("anonymous")
        setMessage("Workspace switch failed. Please sign in again.")
      }
    }
  }
  async function reloadMembership() {
    if (!accessToken) return
    const token = accessToken
    const ticket = clear("booting")
    try {
      await establish(token, ticket)
    } catch {
      if (ticket === transition.current) expireSession()
    }
  }
  return (
    <AuthContext.Provider
      value={{
        status,
        accessToken,
        organizations,
        currentOrganization,
        currentUserId,
        workspaceRevision,
        sessionMessage,
        logoutPending,
        revocationFailed,
        login,
        logout,
        switchOrganization,
        reloadMembership,
        expireSession,
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
