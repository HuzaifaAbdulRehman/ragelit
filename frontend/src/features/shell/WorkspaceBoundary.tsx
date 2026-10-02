import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
} from "react"
import { isAuthenticationError, useAuth } from "../auth/AuthProvider"

const ScopeSignal = createContext<AbortSignal | null>(null)
export function WorkspaceBoundary({ children }: { children: ReactNode }) {
  const { expireSession } = useAuth()
  const [controller] = useState(() => new AbortController())
  const [client] = useState(() => {
    const onError = (error: unknown) => {
      if (!controller.signal.aborted && isAuthenticationError(error))
        expireSession()
    }
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false },
        mutations: { retry: false },
      },
    })
  })
  useEffect(
    () => () => {
      controller.abort()
      void client.cancelQueries()
      client.clear()
    },
    [controller, client],
  )
  return (
    <ScopeSignal.Provider value={controller.signal}>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </ScopeSignal.Provider>
  )
}
export function useWorkspace() {
  const auth = useAuth()
  const signal = useContext(ScopeSignal)
  if (!auth.accessToken || !auth.currentOrganization || !signal)
    throw new Error("No active workspace")
  return {
    token: auth.accessToken,
    organization: auth.currentOrganization,
    key: [auth.currentOrganization.id, auth.workspaceRevision] as const,
    signal,
  }
}
