import { Link, Navigate, Outlet, useNavigate } from "@tanstack/react-router"

import { useAuth } from "../auth/AuthProvider"
import { OrganizationSwitcher } from "./OrganizationSwitcher"
import { WorkspaceBoundary } from "./WorkspaceBoundary"

export function AppShell() {
  const auth = useAuth()
  const navigate = useNavigate()

  if (auth.status === "booting") {
    return <main className="loading-state">Restoring your secure session…</main>
  }
  if (auth.status === "anonymous") return <Navigate to="/login" />

  async function logout() {
    await auth.logout()
    await navigate({ to: "/login" })
  }

  const canManage = ["owner", "admin"].includes(
    auth.currentOrganization?.role ?? "",
  )

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <Link className="brand" to="/">
          RAGelit
        </Link>
        <OrganizationSwitcher />
        <nav aria-label="Workspace navigation">
          <Link to="/">Overview</Link>
          <Link to="/documents">Documents</Link>
          <Link to="/chat">Chat</Link>
          {canManage && auth.currentOrganization ? (
            <Link
              to="/organizations/$organizationId/members"
              params={{ organizationId: auth.currentOrganization.id }}
            >
              People
            </Link>
          ) : null}
          {canManage && auth.currentOrganization && (
            <Link
              to="/organizations/$organizationId/groups"
              params={{ organizationId: auth.currentOrganization.id }}
            >
              Groups
            </Link>
          )}
          <span aria-disabled="true">Audit</span>
        </nav>
        <button className="secondary" type="button" onClick={logout}>
          Log out
        </button>
      </aside>
      <main className="workspace">
        <WorkspaceBoundary
          key={`${auth.currentOrganization?.id}:${auth.workspaceRevision}`}
        >
          <Outlet />
        </WorkspaceBoundary>
      </main>
    </div>
  )
}
