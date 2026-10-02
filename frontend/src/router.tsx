import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
} from "@tanstack/react-router"
import { GroupsPage } from "./features/admin/GroupsPage"
import { PeoplePage } from "./features/admin/PeoplePage"
import { useAuth } from "./features/auth/AuthProvider"
import { LoginPage } from "./features/auth/LoginPage"
import { AppShell } from "./features/shell/AppShell"

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
const dashboardRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: Dashboard,
})
const membersRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/organizations/$organizationId/members",
  component: PeoplePage,
})
const groupsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/organizations/$organizationId/groups",
  component: GroupsPage,
})
const routeTree = rootRoute.addChildren([
  loginRoute,
  shellRoute.addChildren([dashboardRoute, membersRoute, groupsRoute]),
])
export const router = createRouter({ routeTree })
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}
