import { useQuery } from "@tanstack/react-query"
import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  useParams,
} from "@tanstack/react-router"

import { ApiError } from "./api/client"
import { tenancyApi } from "./api/tenancy"
import { useAuth } from "./features/auth/AuthProvider"
import { LoginPage } from "./features/auth/LoginPage"
import { AppShell } from "./features/shell/AppShell"
import { RequestError } from "./features/shell/RequestError"
import { useWorkspace } from "./features/shell/WorkspaceBoundary"

const rootRoute = createRootRoute({ component: () => <Outlet /> })
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
})
const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "shell",
  component: AppShell,
})

function Dashboard() {
  const { currentOrganization } = useAuth()
  return (
    <section>
      <p className="eyebrow">Current workspace</p>
      <h1>{currentOrganization?.name}</h1>
      <p className="lede">Your private document workspace is ready.</p>
      <div className="status-grid">
        <article>
          <span>Access role</span>
          <strong>{currentOrganization?.role}</strong>
        </article>
        <article>
          <span>Tenant boundary</span>
          <strong>Enforced</strong>
        </article>
        <article>
          <span>Session</span>
          <strong>Active</strong>
        </article>
      </div>
    </section>
  )
}

function MembersPage() {
  const { organizationId } = useParams({ strict: false })
  const scope = useWorkspace()
  const members = useQuery({
    queryKey: [...scope.key, "members", organizationId],
    queryFn: ({ signal }) =>
      tenancyApi.members(scope.token, organizationId ?? "", 50, 0, signal),
  })
  if (members.error instanceof ApiError && members.error.status === 404) {
    return (
      <section>
        <h1>Not found</h1>
        <p>The requested resource was not found.</p>
      </section>
    )
  }
  return (
    <section>
      <p className="eyebrow">Access management</p>
      <h1>People</h1>
      <RequestError
        error={members.error}
        retry={() => void members.refetch()}
      />
      <ul className="member-list">
        {members.data?.items.map((member) => (
          <li key={member.id}>
            <span>{member.email}</span>
            <strong>{member.role}</strong>
          </li>
        ))}
      </ul>
    </section>
  )
}

const dashboardRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: Dashboard,
})
const membersRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/organizations/$organizationId/members",
  component: MembersPage,
})
const routeTree = rootRoute.addChildren([
  loginRoute,
  shellRoute.addChildren([dashboardRoute, membersRoute]),
])

export const router = createRouter({ routeTree })

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}
