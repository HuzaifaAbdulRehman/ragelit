import { useNavigate } from "@tanstack/react-router"

import { useAuth } from "../auth/AuthProvider"

export function OrganizationSwitcher() {
  const auth = useAuth()
  const navigate = useNavigate()

  async function change(organizationId: string) {
    if (organizationId === auth.currentOrganization?.id) return
    await auth.switchOrganization(organizationId)
    await navigate({ to: "/" })
  }

  return (
    <label className="organization-control">
      <span className="organization-label">Organization</span>
      <select
        aria-label="Organization switcher"
        value={auth.currentOrganization?.id ?? ""}
        onChange={(event) => change(event.target.value)}
      >
        {auth.organizations.map((organization) => (
          <option key={organization.id} value={organization.id}>
            {organization.name}
          </option>
        ))}
      </select>
    </label>
  )
}
