import { expect, test } from "@playwright/test"
import {
  groupId,
  member,
  mockSession,
  orgA,
  problem,
  signIn,
} from "./helpers/session"

test("people paginate and persist roles and activation", async ({ page }) => {
  await mockSession(page)
  let updated = { ...member }
  await page.route(
    `**/api/v1/organizations/${orgA}/members**`,
    async (route) => {
      const url = new URL(route.request().url())
      if (route.request().method() === "PATCH") {
        const command = route.request().postDataJSON()
        expect(url.pathname).toContain(`/${member.id}/`)
        updated = { ...updated, ...command }
        await route.fulfill({ json: updated })
      } else {
        const offset = Number(url.searchParams.get("offset") ?? 0)
        await route.fulfill({
          json: {
            items:
              offset === 0
                ? Array.from({ length: 50 }, (_, i) =>
                    i === 0
                      ? updated
                      : {
                          ...member,
                          id: `m${i}`,
                          user_id: `u${i}`,
                          email: `person${i}@example.com`,
                        },
                  )
                : [{ ...member, id: "last", email: "last@example.com" }],
            limit: 50,
            offset,
          },
        })
      }
    },
  )
  await signIn(page)
  await page.getByRole("link", { name: "People" }).click()
  await page.getByLabel(`Role for ${member.email}`).selectOption("auditor")
  const roleSaved = page.waitForResponse(
    (response) =>
      response.url().endsWith("/role") &&
      response.request().method() === "PATCH",
  )
  await page
    .getByRole("button", { name: `Save role for ${member.email}` })
    .click()
  await roleSaved
  expect(updated.role).toBe("auditor")
  await expect(page.getByLabel(`Role for ${member.email}`)).toHaveValue(
    "auditor",
  )
  page.on("dialog", (dialog) => dialog.accept())
  await page
    .getByRole("button", { name: `Deactivate ${member.email}`, exact: true })
    .click()
  await expect(
    page.getByRole("button", { name: `Activate ${member.email}`, exact: true }),
  ).toBeVisible()
  await page
    .getByRole("button", { name: `Activate ${member.email}`, exact: true })
    .click()
  await expect(
    page.getByRole("button", {
      name: `Deactivate ${member.email}`,
      exact: true,
    }),
  ).toBeVisible()
  await page.getByRole("button", { name: "Next people" }).click()
  await expect(
    page.getByText("last@example.com", { exact: true }),
  ).toBeVisible()
  await expect(page.getByText(member.email, { exact: true })).toHaveCount(0)
})

test("last owner conflict preserves the role draft", async ({ page }) => {
  await mockSession(page)
  await page.route(`**/api/v1/organizations/${orgA}/members/*/role`, (route) =>
    route.fulfill({
      status: 409,
      json: problem(
        "last_owner_required",
        409,
        "The organization must keep an active owner.",
      ),
    }),
  )
  await signIn(page)
  await page.getByRole("link", { name: "People" }).click()
  await page.getByLabel(`Role for ${member.email}`).selectOption("admin")
  await page
    .getByRole("button", { name: `Save role for ${member.email}` })
    .click()
  await expect(page.getByRole("alert")).toContainText("last active owner")
  await expect(page.getByLabel(`Role for ${member.email}`)).toHaveValue("admin")
})

test("groups create rename delete and update membership IDs", async ({
  page,
}) => {
  await mockSession(page)
  let groups = [{ id: groupId, name: "Engineering" }]
  let joined = false
  await page.route(
    `**/api/v1/organizations/${orgA}/groups**`,
    async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      if (path.includes("/members/")) {
        expect(path).toContain(`/members/${member.id}`)
        joined = request.method() === "POST"
        await route.fulfill({ status: 204 })
      } else if (path.endsWith("/members")) {
        await route.fulfill({
          json: { items: joined ? [member] : [], limit: 50, offset: 0 },
        })
      } else if (request.method() === "GET") {
        await route.fulfill({ json: { items: groups, limit: 50, offset: 0 } })
      } else if (request.method() === "POST") {
        const created = {
          id: "88888888-8888-4888-8888-888888888888",
          name: request.postDataJSON().name,
        }
        groups.push(created)
        await route.fulfill({ status: 201, json: created })
      } else if (request.method() === "PATCH") {
        const id = path.split("/").at(-1)
        groups = groups.map((group) =>
          group.id === id
            ? { ...group, name: request.postDataJSON().name }
            : group,
        )
        await route.fulfill({ json: groups.find((group) => group.id === id) })
      } else {
        groups = groups.filter((group) => group.id !== path.split("/").at(-1))
        await route.fulfill({ status: 204 })
      }
    },
  )
  await signIn(page)
  await page.getByRole("link", { name: "Groups", exact: true }).click()
  await page.getByLabel("New group name").fill("Reviewers")
  await page.getByRole("button", { name: "Create group" }).click()
  await expect(page.getByLabel("Group name for Reviewers")).toBeVisible()
  await page.getByLabel("Group name for Engineering").fill("Security")
  await page.getByRole("button", { name: "Rename Engineering" }).click()
  await expect(page.getByLabel("Group name for Security").first()).toBeVisible()
  await page
    .getByRole("button", { name: "Members for Security" })
    .first()
    .click()
  await page.getByLabel("Add person").selectOption(member.id)
  await page.getByRole("button", { name: "Add member", exact: true }).click()
  await expect(
    page.getByRole("checkbox", { name: member.email, exact: true }),
  ).toBeChecked()
  page.on("dialog", (dialog) => dialog.accept())
  await page.getByRole("checkbox", { name: member.email, exact: true }).click()
  await expect(
    page.getByRole("checkbox", { name: member.email, exact: true }),
  ).toHaveCount(0)
  await page.getByRole("button", { name: "Close members" }).click()
  await page.getByRole("button", { name: "Delete Security" }).first().click()
  await expect(page.getByLabel("Group name for Security")).toHaveCount(0)
  await expect(page.getByLabel("Group name for Reviewers")).toBeVisible()
})

test("member navigation does not offer management", async ({ page }) => {
  await mockSession(page, "member")
  await signIn(page)
  await expect(page.getByRole("navigation")).not.toContainText("People")
  await expect(page.getByRole("navigation")).not.toContainText("Groups")
})
